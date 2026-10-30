"""Focused tests: financial maths, record-type filtering, shop/date filters, rules and CSV validation."""
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analytics as an  # noqa: E402
import app as app_module  # noqa: E402
import recommendations as rec  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "DukaSense_Combined_Synthetic_Dataset.csv"


def sale(i, shop, product, day, qty, price, cost, cat="Staples"):
    rev, cogs = round(qty * price, 2), round(qty * cost, 2)
    return {"record_type": "SALE", "sale_line_id": f"SL{i}", "transaction_id": f"TX{i}", "sale_date": f"{day} 10:00:00",
            "shop_id": shop, "shop_name": f"Shop {shop}", "county": "Nairobi", "product_id": product,
            "product_name": f"Product {product}", "product_category": cat, "product_unit": "1 kg bag",
            "quantity": qty, "unit_selling_price_kes": price, "unit_cost_at_sale_kes": cost,
            "line_revenue_kes": rev, "line_estimated_cogs_kes": cogs, "line_estimated_gross_profit_kes": round(rev - cogs, 2),
            "data_label": "SYNTHETIC DEMO DATA"}


def catalogue(shop, product, price=100.0, cost=80.0):
    return {"record_type": "PRODUCT_CATALOGUE", "shop_id": shop, "product_id": product, "product_name": f"Product {product}",
            "product_category": "Staples", "product_unit": "1 kg bag", "unit_selling_price_kes": price, "unit_cost_kes": cost,
            "data_label": "SYNTHETIC DEMO DATA"}


def inventory(shop, product, on_hand, reorder):
    return {"record_type": "INVENTORY_SNAPSHOT", "shop_id": shop, "product_id": product, "quantity_on_hand": on_hand,
            "reorder_level": reorder, "stock_status": "REORDER" if on_hand <= reorder else "OK",
            "snapshot_date": "2025-12-31", "data_label": "SYNTHETIC DEMO DATA"}


def slow(shop, product, units, flag):
    return {"record_type": "SLOW_MOVING_SUMMARY", "shop_id": shop, "product_id": product, "units_sold_last_90_days": units,
            "slow_moving": flag, "threshold_units": 5, "period_start": "2025-10-03", "period_end": "2025-12-31",
            "data_label": "SYNTHETIC DEMO DATA"}


@pytest.fixture()
def mini_df():
    rows = [
        # SA/P1: strong seller, low stock. Revenue 1000+500, cost 800+400 -> margin 20%
        sale(1, "SA", "P1", "2025-11-01", 10, 100, 80),
        sale(2, "SA", "P1", "2025-12-01", 5, 100, 80),
        # SA/P2: margin 5% (low margin), plenty of stock
        sale(3, "SA", "P2", "2025-11-10", 20, 100, 95),
        # SB/P3: only sold early in the year
        sale(4, "SB", "P3", "2025-02-01", 6, 50, 40),
        catalogue("SA", "P1"), catalogue("SA", "P2"), catalogue("SA", "P4"), catalogue("SB", "P3"),
        inventory("SA", "P1", 2, 5), inventory("SA", "P2", 50, 5), inventory("SA", "P4", 30, 5), inventory("SB", "P3", 20, 5),
        slow("SA", "P1", 15, False), slow("SA", "P2", 20, False), slow("SA", "P4", 0, True), slow("SB", "P3", 0, False),
        # Non-sale rows that must never be counted as sales
        {"record_type": "STOCK_MOVEMENT", "shop_id": "SA", "product_id": "P1", "quantity": 999, "line_revenue_kes": 999999,
         "data_label": "SYNTHETIC DEMO DATA"},
    ]
    return pd.DataFrame(rows)


@pytest.fixture()
def data(mini_df):
    return an.build_dataset(mini_df)


# ------------------------------------------------------- financial calculations
def test_kpis_use_revenue_minus_cogs_and_safe_margin(data):
    k = an.kpis(data.sales)
    assert k["revenue_kes"] == 1000 + 500 + 2000 + 300
    assert k["estimated_cogs_kes"] == 800 + 400 + 1900 + 240
    assert k["gross_profit_kes"] == pytest.approx(k["revenue_kes"] - k["estimated_cogs_kes"])
    assert k["gross_margin_pct"] == pytest.approx(k["gross_profit_kes"] / k["revenue_kes"] * 100, abs=0.01)
    assert k["units_sold"] == 41


def test_zero_revenue_margin_is_none_not_error(data):
    empty = data.sales.iloc[0:0]
    k = an.kpis(empty)
    assert k["revenue_kes"] == 0 and k["gross_margin_pct"] is None


def test_real_dataset_matches_cleaning_notebook():
    d = an.load_csv(DEMO)
    k = an.kpis(d.sales)
    assert len(d.sales) == 13450
    assert k["revenue_kes"] == pytest.approx(3490196.32, abs=0.01)
    assert k["gross_profit_kes"] == pytest.approx(662811.57, abs=0.01)
    assert k["gross_margin_pct"] == pytest.approx(18.99, abs=0.01)


# ------------------------------------------------------ record-type filtering
def test_only_sale_rows_count_as_sales(mini_df, data):
    assert len(data.sales) == 4  # the STOCK_MOVEMENT row with revenue 999999 is excluded
    assert len(mini_df) > len(data.sales)
    assert data.sales["line_revenue_kes"].sum() < 999999
    assert data.movement_count == 1 and len(data.inventory) == 4 and len(data.slow) == 4


# --------------------------------------------------------- shop and date filters
def test_shop_and_date_filters(data):
    assert len(an.filter_sales(data, "SA")) == 3
    assert len(an.filter_sales(data, "SB")) == 1
    f = an.resolve_filters(data, "SA", "2025-11-15", "2025-12-31")
    sub = an.filter_sales(data, f["shop"], f["start"], f["end"])
    assert list(sub["sale_line_id"]) == ["SL2"]
    assert f["days"] == 47


def test_bad_filters_raise(data):
    with pytest.raises(an.DataError):
        an.resolve_filters(data, "NOPE", None, None)
    with pytest.raises(an.DataError):
        an.resolve_filters(data, None, "not-a-date", None)
    with pytest.raises(an.DataError):
        an.resolve_filters(data, None, "2025-12-01", "2025-01-01")


# -------------------------------------------------------------------- rules
def _recs(data, shop=None, start=None, end=None):
    f = an.resolve_filters(data, shop, start, end)
    sales = an.filter_sales(data, f["shop"], f["start"], f["end"])
    inv = an.inventory_view(data, f["shop"], sales, f["days"])
    return rec.generate(inv, f["days"], "test period")


def test_low_stock_and_replenishment_rules(data):
    recs = _recs(data, "SA", "2025-10-03", "2025-12-31")
    low = [r for r in recs if r["type"] == "low_stock"]
    assert [r["product"] for r in low] == ["Product P1"]  # P2 and P4 are above reorder level
    assert low[0]["priority"] == "medium"  # 15 units in 90 days, 2 left: about 12 days of stock, not under 7
    repl = [r for r in recs if r["type"] == "replenishment"]
    assert repl and "no order has been placed" in repl[0]["suggested_action"]
    assert repl[0]["metrics"]["suggested_review_quantity"] > 0


def test_low_margin_uses_configurable_threshold(data):
    recs = _recs(data, "SA")
    assert [r["product"] for r in recs if r["type"] == "low_margin"] == ["Product P2"]  # 5% margin < 10%
    inv = an.inventory_view(data, "SA", data.sales[data.sales["shop_id"] == "SA"], 365)
    none = rec.generate(inv, 365, "x", {"low_margin_pct": 1.0})
    assert not [r for r in none if r["type"] == "low_margin"]


def test_slow_moving_uses_supplied_flag(data):
    recs = _recs(data, "SA")
    slow_recs = [r for r in recs if r["type"] == "slow_moving"]
    assert [r["product"] for r in slow_recs] == ["Product P4"]
    assert slow_recs[0]["priority"] == "medium"  # 30 on hand > 2 x reorder level of 5


def test_strong_seller_ranks_by_units_in_period(data):
    recs = _recs(data, "SA")
    strong = [r["product"] for r in recs if r["type"] == "strong_seller"]
    assert strong == ["Product P2"]  # 20 units beats P1's 15 in the whole period
    narrow = _recs(data, "SA", "2025-12-01", "2025-12-31")
    assert [r["product"] for r in narrow if r["type"] == "strong_seller"] == ["Product P1"]


def test_recommendations_respond_to_shop(data):
    assert {r["shop_id"] for r in _recs(data, "SA")} == {"SA"}
    assert {r["shop_id"] for r in _recs(data, "SB")} <= {"SB"}
    assert {r["shop_id"] for r in _recs(data)} == {"SA", "SB"}  # All Shops covers both


def test_few_sales_means_no_quantity_suggested():
    df = pd.DataFrame([sale(1, "SA", "P1", "2025-12-01", 2, 100, 80), catalogue("SA", "P1"),
                       inventory("SA", "P1", 1, 5), slow("SA", "P1", 2, True)])
    d = an.build_dataset(df)
    recs = _recs(d)
    types = {r["type"] for r in recs}
    assert "low_stock" in types and "replenishment" not in types
    assert [r for r in recs if r["type"] == "low_stock"][0]["priority"] == "low"


# ----------------------------------------------------------- CSV validation
def test_valid_dataset_passes(mini_df):
    assert an.validate_dataframe(mini_df) == []


def test_missing_record_type_column():
    assert an.validate_dataframe(pd.DataFrame({"a": [1]})) == ["Missing required column: record_type"]


def test_missing_required_record_types_and_columns(mini_df):
    errs = an.validate_dataframe(mini_df[mini_df.record_type != "INVENTORY_SNAPSHOT"])
    assert any("INVENTORY_SNAPSHOT" in e for e in errs)
    errs = an.validate_dataframe(mini_df.drop(columns=["line_revenue_kes"]))
    assert any("line_revenue_kes" in e for e in errs)


def test_bad_values_are_rejected(mini_df):
    bad = mini_df.copy()
    bad.loc[0, "quantity"] = -3
    assert any("negative quantities" in e for e in an.validate_dataframe(bad))
    bad = mini_df.copy()
    bad.loc[0, "line_revenue_kes"] = 1
    assert any("do not match" in e for e in an.validate_dataframe(bad))
    bad = mini_df.copy()
    bad.loc[0, "sale_date"] = "garbage"
    assert any("sale_date" in e for e in an.validate_dataframe(bad))
    bad = mini_df.copy()
    bad.loc[bad.index[0], "record_type"] = "MYSTERY"
    assert any("Unsupported record_type" in e for e in an.validate_dataframe(bad))


# ------------------------------------------------------------ API and upload
@pytest.fixture()
def client():
    app_module.load_demo()
    app_module.app.config["TESTING"] = True
    yield app_module.app.test_client()
    app_module.load_demo()


def test_api_basics(client):
    assert client.get("/api/health").get_json()["rows"]["sales"] == 13450
    assert len(client.get("/api/shops").get_json()["shops"]) == 12
    d = client.get("/api/dashboard?shop=SH001&start=2025-10-03&end=2025-12-31").get_json()
    assert d["meta"]["is_live"] is False and d["meta"]["inventory_snapshot_date"] == "2025-12-31"
    assert d["kpis"]["revenue_kes"] > 0
    assert client.get("/api/dashboard?shop=ZZ").status_code == 400
    assert client.get("/api/recommendations").get_json()["summary"]["total"] > 0


def test_upload_rejects_bad_file_and_keeps_active_data(client):
    r = client.post("/api/upload", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")}, content_type="multipart/form-data")
    assert r.status_code == 422 and r.get_json()["error"]["details"]
    assert client.get("/api/health").get_json()["source"] == "demo"
    r = client.post("/api/upload", data={"file": (io.BytesIO(b"x"), "x.xlsx")}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_upload_accepts_valid_file_and_restore_works(client, mini_df):
    buf = io.BytesIO(mini_df.to_csv(index=False).encode())
    r = client.post("/api/upload", data={"file": (buf, "mini.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200
    h = client.get("/api/health").get_json()
    assert h["source"] == "upload" and h["rows"]["sales"] == 4
    assert client.post("/api/restore").status_code == 200
    assert client.get("/api/health").get_json()["rows"]["sales"] == 13450


# ------------------------------------------------- starts empty, loads on upload
def test_app_starts_empty_and_loads_on_upload(mini_df):
    app_module.STATE.update(data=None, source=None, filename=None)
    c = app_module.app.test_client()
    h = c.get("/api/health").get_json()
    assert h["dataset_loaded"] is False
    r = c.get("/api/dashboard")
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "no_data"
    buf = io.BytesIO(mini_df.to_csv(index=False).encode())
    assert c.post("/api/upload", data={"file": (buf, "mini.csv")}, content_type="multipart/form-data").status_code == 200
    assert c.get("/api/health").get_json()["dataset_loaded"] is True
    assert c.get("/api/dashboard").status_code == 200
    assert c.post("/api/unload").status_code == 200
    assert c.get("/api/health").get_json()["dataset_loaded"] is False
    assert c.post("/api/sample").status_code == 200
    assert c.get("/api/health").get_json()["rows"]["sales"] == 13450
    app_module.load_demo()
