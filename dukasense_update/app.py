"""DukaSense Flask app: routes, dataset loading and CSV upload handling.

Demo notes: the active dataset lives in memory, is shared by everyone using this
instance (there is no authentication or per-user isolation) and resets on restart.
"""
from __future__ import annotations

import io
import os
import threading
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

import analytics as an
import recommendations as rec

BASE = Path(__file__).parent
DEMO_CSV = BASE / "DukaSense_Combined_Synthetic_Dataset.csv"
MAX_UPLOAD_MB = 25

app = Flask(__name__, static_folder=str(BASE / "frontend"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

_lock = threading.Lock()
STATE = {"data": None, "source": None, "filename": None}


class ApiError(Exception):
    def __init__(self, message, status=400, code="bad_request", details=None):
        super().__init__(message)
        self.message, self.status, self.code, self.details = message, status, code, details


def load_demo() -> None:
    data = an.load_csv(DEMO_CSV)
    with _lock:
        STATE.update(data=data, source="demo", filename=DEMO_CSV.name)


def current() -> an.Dataset:
    data = STATE["data"]
    if data is None:
        raise ApiError("No dataset is loaded yet. Upload a CSV or load the sample data.", 503, "no_data")
    return data


# ------------------------------------------------------------ error handling
@app.errorhandler(ApiError)
def _api_error(e):
    body = {"error": {"code": e.code, "message": e.message}}
    if e.details:
        body["error"]["details"] = e.details
    return jsonify(body), e.status


@app.errorhandler(an.DataError)
def _data_error(e):
    return jsonify({"error": {"code": "invalid_request", "message": str(e)}}), 400


@app.errorhandler(404)
def _not_found(e):
    return jsonify({"error": {"code": "not_found", "message": "Not found."}}), 404


@app.errorhandler(413)
def _too_large(e):
    return jsonify({"error": {"code": "file_too_large",
                              "message": f"The file is larger than {MAX_UPLOAD_MB} MB."}}), 413


@app.errorhandler(405)
def _method(e):
    return jsonify({"error": {"code": "method_not_allowed", "message": "Method not allowed."}}), 405


@app.errorhandler(Exception)
def _unexpected(e):
    app.logger.exception("Unhandled error")
    return jsonify({"error": {"code": "server_error", "message": "Something went wrong on the server."}}), 500


# ----------------------------------------------------------------- helpers
def _filters(data):
    a = request.args
    return an.resolve_filters(data, a.get("shop"), a.get("start"), a.get("end"))


def _meta(data, f) -> dict:
    snap = data.inventory["snapshot_date"].max()
    shop_name = None
    if f["shop"]:
        shop_name = data.sales.loc[data.sales["shop_id"] == f["shop"], "shop_name"].iloc[0]
    return {
        "data_label": data.label,
        "source": STATE["source"],
        "filename": STATE["filename"],
        "is_live": False,
        "shop_id": f["shop"],
        "shop_name": shop_name or "All Shops",
        "period": {"start": f["eff_start"].strftime("%Y-%m-%d"), "end": f["eff_end"].strftime("%Y-%m-%d"),
                   "days": f["days"], "dataset_start": f["data_min"].strftime("%Y-%m-%d"),
                   "dataset_end": f["data_max"].strftime("%Y-%m-%d")},
        "inventory_snapshot_date": None if pd.isna(snap) else snap.strftime("%Y-%m-%d"),
        "notes": [
            "Synthetic, historical demo data. Not live.",
            "Stock levels come from a single inventory snapshot and do not change with the date filter.",
            "Gross profit is revenue minus estimated COGS. It is not net profit.",
            "Recommendations use simple rules, not a trained model or a forecast.",
        ],
    }


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    data = STATE["data"]
    if data is None:
        return jsonify({"status": "ok", "dataset_loaded": False, "source": None, "filename": None})
    return jsonify({
        "status": "ok", "dataset_loaded": True, "source": STATE["source"], "filename": STATE["filename"],
        "rows": {"sales": len(data.sales), "catalogue": len(data.catalogue),
                 "inventory_snapshot": len(data.inventory), "slow_moving_summary": len(data.slow),
                 "stock_movement": data.movement_count},
        "shops": int(data.sales["shop_id"].nunique()),
        "data_label": data.label,
    })


@app.get("/api/shops")
def shops():
    return jsonify({"shops": an.list_shops(current())})


@app.get("/api/dashboard")
def dashboard():
    data = current()
    f = _filters(data)
    sales = an.filter_sales(data, f["shop"], f["start"], f["end"])
    if request.args.get("sort", "revenue_kes") not in ("revenue_kes", "units_sold", "gross_profit_kes"):
        raise ApiError("sort must be revenue_kes, units_sold or gross_profit_kes.", 400, "invalid_request")
    body = {
        "meta": _meta(data, f),
        "kpis": an.kpis(sales),
        "trend": an.trend(sales, request.args.get("granularity", "auto"), f["days"]),
        "categories": an.category_performance(sales),
        "top_products": an.product_performance(sales, limit=int(request.args.get("limit", 10)),
                                               sort=request.args.get("sort", "revenue_kes")),
        "shop_comparison": an.shop_comparison(data, an.filter_sales(data, None, f["start"], f["end"])),
    }
    return jsonify(body)


@app.get("/api/products")
def products():
    data = current()
    f = _filters(data)
    sales = an.filter_sales(data, f["shop"], f["start"], f["end"])
    inv = an.inventory_view(data, f["shop"], sales, f["days"])
    a = request.args
    if a.get("search"):
        q = a["search"].strip().lower()
        inv = inv[inv["product_name"].str.lower().str.contains(q, regex=False)
                  | inv["product_category"].str.lower().str.contains(q, regex=False)]
    if a.get("status"):
        status = a["status"].upper()
        if status not in ("OK", "REORDER"):
            raise ApiError("status must be OK or REORDER.", 400, "invalid_request")
        inv = inv[inv["stock_status"] == status]
    if a.get("category"):
        inv = inv[inv["product_category"] == a["category"]]
    if a.get("slow") in ("1", "true"):
        inv = inv[inv["slow_flag"].fillna(False).astype(bool)]
    sort = a.get("sort", "product_name")
    if sort not in inv.columns:
        raise ApiError(f"Cannot sort by {sort}.", 400, "invalid_request")
    inv = inv.sort_values(sort, ascending=a.get("order", "asc") != "desc", kind="stable")
    cats = sorted(data.catalogue["product_category"].dropna().unique())
    cols = ["shop_id", "shop_name", "product_id", "product_name", "product_category", "product_unit",
            "quantity_on_hand", "reorder_level", "stock_status", "stock_cover_days", "units_sold",
            "revenue_kes", "gross_profit_kes", "gross_margin_pct", "slow_flag", "snapshot_date"]
    out = inv[cols].copy()
    out["slow_flag"] = out["slow_flag"].astype(object).where(out["slow_flag"].notna(), None)
    return jsonify({
        "meta": _meta(data, f),
        "categories": cats,
        "count": int(len(out)),
        "reorder_count": int((out["stock_status"] == "REORDER").sum()),
        "products": an.to_records(out),
    })


@app.get("/api/recommendations")
def recommendations():
    data = current()
    f = _filters(data)
    sales = an.filter_sales(data, f["shop"], f["start"], f["end"])
    inv = an.inventory_view(data, f["shop"], sales, f["days"])
    label = f"{f['eff_start']:%d %b %Y} to {f['eff_end']:%d %b %Y}"
    recs = rec.generate(inv, f["days"], label)
    summary = rec.summarise(recs)
    a = request.args
    if a.get("type"):
        recs = [r for r in recs if r["type"] == a["type"]]
    if a.get("priority"):
        recs = [r for r in recs if r["priority"] == a["priority"]]
    return jsonify({
        "meta": _meta(data, f),
        "summary": summary,
        "thresholds": rec.CONFIG,
        "recommendations": recs,
    })


@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Choose a CSV file to upload (form field name: file).", 400, "no_file")
    if not file.filename.lower().endswith(".csv"):
        raise ApiError("Only .csv files in the DukaSense combined format are supported.", 400, "wrong_type")
    raw = file.read()
    if not raw.strip():
        raise ApiError("The uploaded file is empty.", 422, "invalid_dataset")
    try:
        df = pd.read_csv(io.StringIO(raw.decode("utf-8-sig")), low_memory=False)
    except UnicodeDecodeError:
        raise ApiError("The file is not valid UTF-8 text.", 422, "invalid_dataset")
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise ApiError(f"The file could not be read as CSV: {e}", 422, "invalid_dataset")
    errors = an.validate_dataframe(df)
    if errors:
        raise ApiError("The upload was rejected. The active dataset was not changed.", 422,
                       "invalid_dataset", errors)
    data = an.build_dataset(df)
    with _lock:
        STATE.update(data=data, source="upload", filename=file.filename)
    return jsonify({"status": "accepted", "filename": file.filename,
                    "rows": {"sales": len(data.sales), "catalogue": len(data.catalogue),
                             "inventory_snapshot": len(data.inventory)},
                    "shops": int(data.sales["shop_id"].nunique()),
                    "message": "Dataset replaced in memory for this demo instance. It resets on restart."})


@app.post("/api/restore")
@app.post("/api/sample")
def restore():
    load_demo()
    return jsonify({"status": "restored", "filename": STATE["filename"],
                    "message": "Sample dataset loaded (synthetic demo data)."})


@app.post("/api/unload")
def unload():
    with _lock:
        STATE.update(data=None, source=None, filename=None)
    return jsonify({"status": "cleared", "message": "Dataset cleared. Upload a CSV to continue."})


# The app starts empty and fills in when a CSV is uploaded.
# Set DUKASENSE_AUTOLOAD=1 to preload the bundled sample file instead.
if os.environ.get("DUKASENSE_AUTOLOAD") == "1":
    load_demo()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
