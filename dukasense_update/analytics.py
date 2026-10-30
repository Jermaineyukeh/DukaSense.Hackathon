"""Pandas processing, validation, filtering and KPI calculations for DukaSense.

All numbers shown in the UI come from here. Every function filters by
`record_type` first, so the combined CSV is never counted as sales by mistake.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

SALE = "SALE"
CATALOGUE = "PRODUCT_CATALOGUE"
INVENTORY = "INVENTORY_SNAPSHOT"
MOVEMENT = "STOCK_MOVEMENT"
SLOW = "SLOW_MOVING_SUMMARY"
RECORD_TYPES = {SALE, CATALOGUE, INVENTORY, MOVEMENT, SLOW}

COMMON_COLUMNS = ["record_type", "shop_id", "product_id"]
REQUIRED_COLUMNS = {
    SALE: [
        "sale_line_id", "sale_date", "shop_name", "product_name", "product_category",
        "product_unit", "quantity", "unit_selling_price_kes", "unit_cost_at_sale_kes",
        "line_revenue_kes", "line_estimated_cogs_kes", "line_estimated_gross_profit_kes",
    ],
    CATALOGUE: ["product_name", "product_category", "product_unit",
                "unit_selling_price_kes", "unit_cost_kes"],
    INVENTORY: ["quantity_on_hand", "reorder_level", "stock_status", "snapshot_date"],
    SLOW: ["units_sold_last_90_days", "slow_moving", "threshold_units",
           "period_start", "period_end"],
}
SALE_NUMERIC = ["quantity", "unit_selling_price_kes", "unit_cost_at_sale_kes",
                "line_revenue_kes", "line_estimated_cogs_kes",
                "line_estimated_gross_profit_kes"]
BOOL_MAP = {"true": True, "false": False, "1": True, "0": False, "1.0": True, "0.0": False}


class DataError(ValueError):
    """Raised for bad query parameters or an unusable dataset."""


@dataclass
class Dataset:
    sales: pd.DataFrame
    catalogue: pd.DataFrame
    inventory: pd.DataFrame
    slow: pd.DataFrame
    movement_count: int
    label: str


# ---------------------------------------------------------------- helpers
def safe_pct(numerator, denominator):
    """numerator / denominator * 100, or None when the denominator is zero."""
    if denominator is None or denominator == 0 or pd.isna(denominator):
        return None
    return round(float(numerator) / float(denominator) * 100, 2)


def _to_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", format="ISO8601")


def _bool_flag(series: pd.Series) -> pd.Series:
    mapped = series.astype("string").str.lower().str.strip().map(BOOL_MAP)
    return mapped.astype("boolean")


# ------------------------------------------------------------- validation
def validate_dataframe(df: pd.DataFrame) -> list[str]:
    """Return a list of human readable problems. An empty list means usable."""
    if "record_type" not in df.columns:
        return ["Missing required column: record_type"]
    if df.empty:
        return ["The file has no rows."]

    errors: list[str] = []
    types = set(df["record_type"].dropna().astype(str).unique())
    unknown = types - RECORD_TYPES
    if unknown:
        errors.append("Unsupported record_type value(s): " + ", ".join(sorted(unknown)))
    for rt in (SALE, CATALOGUE, INVENTORY):
        if rt not in types:
            errors.append(f"No {rt} rows found; this record type is required.")

    check = [rt for rt in (SALE, CATALOGUE, INVENTORY, SLOW) if rt in types]
    for rt in check:
        missing = [c for c in COMMON_COLUMNS + REQUIRED_COLUMNS[rt] if c not in df.columns]
        if missing:
            errors.append(f"{rt} rows need column(s) missing from the file: {', '.join(missing)}")
    if errors:
        return errors

    sales = df[df["record_type"] == SALE]
    nums = sales[SALE_NUMERIC].apply(pd.to_numeric, errors="coerce")
    bad = int(nums.isna().any(axis=1).sum())
    if bad:
        errors.append(f"{bad} SALE row(s) have missing or non-numeric quantity, price, cost or amount values.")
    else:
        if (nums["quantity"] <= 0).any():
            errors.append("SALE rows contain zero or negative quantities.")
        if ((nums["unit_selling_price_kes"] < 0) | (nums["unit_cost_at_sale_kes"] < 0)).any():
            errors.append("SALE rows contain negative prices or costs.")
        rev_gap = (nums["line_revenue_kes"] - nums["quantity"] * nums["unit_selling_price_kes"]).abs() > 0.01
        cogs_gap = (nums["line_estimated_cogs_kes"] - nums["quantity"] * nums["unit_cost_at_sale_kes"]).abs() > 0.01
        gp_gap = (nums["line_estimated_gross_profit_kes"]
                  - (nums["line_revenue_kes"] - nums["line_estimated_cogs_kes"])).abs() > 0.01
        inconsistent = int((rev_gap | cogs_gap | gp_gap).sum())
        if inconsistent:
            errors.append(f"{inconsistent} SALE row(s) have revenue, COGS or gross profit that do not match quantity x price/cost.")
    if int(_to_dt(sales["sale_date"]).isna().sum()):
        errors.append("Some SALE rows have missing or invalid sale_date values.")
    if sales["sale_line_id"].duplicated().any():
        errors.append("Duplicate sale_line_id values found in SALE rows.")
    if sales["shop_id"].isna().any() or sales["product_id"].isna().any():
        errors.append("SALE rows must all have shop_id and product_id.")

    inv = df[df["record_type"] == INVENTORY]
    if inv[["quantity_on_hand", "reorder_level"]].apply(pd.to_numeric, errors="coerce").isna().any().any():
        errors.append("INVENTORY_SNAPSHOT rows have missing or non-numeric quantity_on_hand or reorder_level.")
    if inv.duplicated(["shop_id", "product_id"]).any():
        errors.append("INVENTORY_SNAPSHOT has more than one row for the same shop and product.")
    if int(_to_dt(inv["snapshot_date"]).isna().sum()):
        errors.append("INVENTORY_SNAPSHOT rows have missing or invalid snapshot_date values.")
    cat = df[df["record_type"] == CATALOGUE]
    if cat.duplicated(["shop_id", "product_id"]).any():
        errors.append("PRODUCT_CATALOGUE has more than one row for the same shop and product.")

    if SLOW in types:
        sl = df[df["record_type"] == SLOW]
        if sl[["units_sold_last_90_days", "threshold_units"]].apply(pd.to_numeric, errors="coerce").isna().any().any():
            errors.append("SLOW_MOVING_SUMMARY rows have missing or non-numeric units or threshold values.")
        if _bool_flag(sl["slow_moving"]).isna().any():
            errors.append("SLOW_MOVING_SUMMARY has slow_moving values that are not true/false.")
    return errors


# ----------------------------------------------------------------- loading
def build_dataset(df: pd.DataFrame) -> Dataset:
    """Split the combined frame by record type and convert column types."""
    errors = validate_dataframe(df)
    if errors:
        raise DataError("; ".join(errors))

    sales = df[df["record_type"] == SALE].copy()
    sales["sale_date"] = _to_dt(sales["sale_date"])
    sales[SALE_NUMERIC] = sales[SALE_NUMERIC].apply(pd.to_numeric)
    sales["day"] = sales["sale_date"].dt.normalize()
    sales["month"] = sales["sale_date"].dt.strftime("%Y-%m")
    if "county" not in sales.columns:
        sales["county"] = None
    sales["county"] = sales["county"].astype(object).where(sales["county"].notna(), None)

    catalogue = df[df["record_type"] == CATALOGUE].copy()
    catalogue[["unit_selling_price_kes", "unit_cost_kes"]] = catalogue[
        ["unit_selling_price_kes", "unit_cost_kes"]].apply(pd.to_numeric, errors="coerce")

    inventory = df[df["record_type"] == INVENTORY].copy()
    inventory[["quantity_on_hand", "reorder_level"]] = inventory[
        ["quantity_on_hand", "reorder_level"]].apply(pd.to_numeric)
    inventory["snapshot_date"] = _to_dt(inventory["snapshot_date"])

    slow = df[df["record_type"] == SLOW].copy()
    if len(slow):
        slow["slow_flag"] = _bool_flag(slow["slow_moving"])
        for c in ("units_sold_last_90_days", "threshold_units"):
            slow[c] = pd.to_numeric(slow[c])
        for c in ("period_start", "period_end", "last_sale_date"):
            slow[c] = _to_dt(slow[c]) if c in slow.columns else pd.NaT
    labels = df["data_label"].dropna().astype(str) if "data_label" in df.columns else pd.Series(dtype=str)
    label = labels.iloc[0] if len(labels) else "DATA LABEL NOT SUPPLIED"
    return Dataset(sales, catalogue, inventory, slow,
                   int((df["record_type"] == MOVEMENT).sum()), label)


def load_csv(path_or_buffer) -> Dataset:
    return build_dataset(pd.read_csv(path_or_buffer, low_memory=False))


# ---------------------------------------------------------------- filters
def parse_date(value, name):
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(pd.to_datetime(value, format="%Y-%m-%d")).normalize()
    except (ValueError, TypeError):
        raise DataError(f"{name} must be a date in YYYY-MM-DD format.")


def resolve_filters(data: Dataset, shop, start, end) -> dict:
    """Validate shop/date parameters and work out the effective reporting period."""
    shop = None if shop in (None, "", "all", "ALL") else shop
    if shop is not None and shop not in set(data.sales["shop_id"]):
        raise DataError(f"Unknown shop_id: {shop}")
    s, e = parse_date(start, "start"), parse_date(end, "end")
    if s is not None and e is not None and s > e:
        raise DataError("start date must not be after end date.")
    data_min, data_max = data.sales["day"].min(), data.sales["day"].max()
    eff_s, eff_e = s or data_min, e or data_max
    return {"shop": shop, "start": s, "end": e, "eff_start": eff_s, "eff_end": eff_e,
            "days": int((eff_e - eff_s).days) + 1, "data_min": data_min, "data_max": data_max}


def filter_sales(data: Dataset, shop=None, start=None, end=None) -> pd.DataFrame:
    s = data.sales
    if shop:
        s = s[s["shop_id"] == shop]
    if start is not None:
        s = s[s["day"] >= start]
    if end is not None:
        s = s[s["day"] <= end]
    return s


# -------------------------------------------------------------------- KPIs
def kpis(sales: pd.DataFrame) -> dict:
    revenue = float(sales["line_revenue_kes"].sum())
    cogs = float(sales["line_estimated_cogs_kes"].sum())
    gross_profit = revenue - cogs
    return {
        "revenue_kes": round(revenue, 2),
        "estimated_cogs_kes": round(cogs, 2),
        "gross_profit_kes": round(gross_profit, 2),
        "gross_margin_pct": safe_pct(gross_profit, revenue),
        "units_sold": float(sales["quantity"].sum()),
        "sale_lines": int(len(sales)),
        "transactions": int(sales["transaction_id"].nunique()) if "transaction_id" in sales else None,
    }


def _group_metrics(sales: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    g = sales.groupby(keys, as_index=False).agg(
        units_sold=("quantity", "sum"),
        revenue_kes=("line_revenue_kes", "sum"),
        cogs_kes=("line_estimated_cogs_kes", "sum"),
        sale_lines=("sale_line_id", "count"),
    )
    g["gross_profit_kes"] = g["revenue_kes"] - g["cogs_kes"]
    g["gross_margin_pct"] = [safe_pct(p, r) for p, r in zip(g["gross_profit_kes"], g["revenue_kes"])]
    return g


def trend(sales: pd.DataFrame, granularity: str = "auto", span_days: int = 0) -> dict:
    if granularity == "auto":
        granularity = "month" if span_days > 92 else "day"
    if granularity not in ("day", "month"):
        raise DataError("granularity must be auto, day or month.")
    key = "month" if granularity == "month" else "day"
    g = _group_metrics(sales, [key]).sort_values(key)
    g["period"] = g[key].dt.strftime("%Y-%m-%d") if key == "day" else g[key]
    rows = g[["period", "revenue_kes", "gross_profit_kes", "units_sold"]].round(2)
    return {"granularity": granularity, "points": rows.to_dict("records")}


def category_performance(sales: pd.DataFrame) -> list[dict]:
    g = _group_metrics(sales, ["product_category"]).sort_values("revenue_kes", ascending=False)
    return g.drop(columns=["cogs_kes"]).round(2).to_dict("records")


def product_performance(sales: pd.DataFrame, limit: int = 10, sort: str = "revenue_kes") -> list[dict]:
    """Products combined across the selected shops (grouped by name, category, unit)."""
    g = _group_metrics(sales, ["product_name", "product_category", "product_unit"])
    return g.sort_values(sort, ascending=False).head(limit).drop(columns=["cogs_kes"]).round(2).to_dict("records")


def shop_comparison(data: Dataset, sales: pd.DataFrame) -> list[dict]:
    g = _group_metrics(sales, ["shop_id", "shop_name"])
    counties = data.sales.drop_duplicates("shop_id").set_index("shop_id")["county"]
    g["county"] = g["shop_id"].map(counties)
    g = g.sort_values("revenue_kes", ascending=False).drop(columns=["cogs_kes"])
    return g.round(2).astype(object).where(g.notna(), None).to_dict("records")


def list_shops(data: Dataset) -> list[dict]:
    s = data.sales.drop_duplicates("shop_id")[["shop_id", "shop_name", "county"]].sort_values("shop_id")
    return s.astype(object).where(s.notna(), None).to_dict("records")


# -------------------------------------------------------------- inventory
def inventory_view(data: Dataset, shop, sales_in_period: pd.DataFrame, days: int) -> pd.DataFrame:
    """One row per shop-product: catalogue + recorded snapshot + period sales + slow-moving summary."""
    keys = ["shop_id", "product_id"]
    cat = data.catalogue[keys + ["product_name", "product_category", "product_unit",
                                 "unit_selling_price_kes", "unit_cost_kes"]]
    inv = data.inventory[keys + ["quantity_on_hand", "reorder_level", "stock_status", "snapshot_date"]]
    t = cat.merge(inv, on=keys, how="inner")

    m = _group_metrics(sales_in_period, keys)[keys + ["units_sold", "revenue_kes", "gross_profit_kes",
                                                       "gross_margin_pct", "sale_lines"]]
    t = t.merge(m, on=keys, how="left")
    for c in ("units_sold", "revenue_kes", "gross_profit_kes", "sale_lines"):
        t[c] = t[c].fillna(0)

    if len(data.slow):
        sl = data.slow[keys + ["slow_flag", "units_sold_last_90_days", "threshold_units",
                               "period_start", "period_end", "last_sale_date"]]
        sl = sl.rename(columns={"units_sold_last_90_days": "slow_units_90", "threshold_units": "slow_threshold",
                                "period_start": "slow_period_start", "period_end": "slow_period_end"})
        t = t.merge(sl, on=keys, how="left")
    else:
        for c in ("slow_flag", "slow_units_90", "slow_threshold", "slow_period_start", "slow_period_end", "last_sale_date"):
            t[c] = pd.NA

    names = data.sales.drop_duplicates("shop_id").set_index("shop_id")["shop_name"]
    t["shop_name"] = t["shop_id"].map(names)
    if shop:
        t = t[t["shop_id"] == shop]
    t = t.copy()

    daily = t["units_sold"] / max(days, 1)
    t["stock_cover_days"] = [round(q / d, 1) if d > 0 else None for q, d in zip(t["quantity_on_hand"], daily)]
    t["stock_status"] = t["stock_status"].astype("string").str.upper()
    return t.reset_index(drop=True)


def to_records(df: pd.DataFrame) -> list[dict]:
    """JSON safe records: NaN/NaT/NA become None, timestamps become ISO dates."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(out.notna(), None)
    rows = out.to_dict("records")
    for r in rows:
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
            elif hasattr(v, "item"):
                r[k] = v.item()
    return rows
