"""Transparent, rule-based recommendations for DukaSense.

These are simple rules over recorded data. They are not a trained model and not a forecast.
Every recommendation carries the numbers that triggered it so the owner can check it.
"""
from __future__ import annotations

import math

import pandas as pd

# Configurable demo thresholds.
CONFIG = {
    "low_margin_pct": 10.0,        # gross margin below this is flagged
    "strong_top_share": 0.20,      # top 20% of a shop's products by units sold
    "strong_min_units": 5,         # ...and at least this many units in the period
    "min_units_for_quantity": 5,   # below this, no restock quantity is suggested
    "target_cover_days": 14,       # restock quantity aims at this many days of recent sales
    "urgent_cover_days": 7,        # under this many days of stock left is urgent
    "overstock_multiple": 2,       # stock above this x reorder level counts as overstocked
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
TYPE_ORDER = {"low_stock": 0, "replenishment": 1, "low_margin": 2, "slow_moving": 3, "strong_seller": 4}
GROSS_PROFIT_NOTE = "Gross profit is before rent, wages, transport and electricity; it is not net profit."


def _confidence(units: float, lines: float) -> str:
    if units >= 20 and lines >= 4:
        return "HIGH"
    if units >= 5:
        return "MEDIUM"
    return "LOW"


def _num(x) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def _rec(r, rtype, priority, reason, action, metrics):
    return {
        "id": f"{rtype}:{r.shop_id}:{r.product_id}",
        "type": rtype,
        "priority": priority,
        "shop_id": r.shop_id,
        "shop_name": r.shop_name,
        "product_id": r.product_id,
        "product": r.product_name,
        "reason": reason,
        "suggested_action": action,
        "confidence": _confidence(r.units_sold, r.sale_lines),
        "metrics": metrics,
    }


def generate(inv: pd.DataFrame, days: int, period_label: str, config: dict | None = None) -> list[dict]:
    """Build recommendations from an inventory_view table (already filtered to shop and period)."""
    cfg = {**CONFIG, **(config or {})}
    recs: list[dict] = []
    if inv.empty:
        return recs
    days = max(int(days), 1)

    # Strong sellers are ranked within each shop by units sold in the selected period.
    inv = inv.copy()
    inv["unit_rank"] = inv.groupby("shop_id")["units_sold"].rank(ascending=False, method="min")
    inv["strong_cutoff"] = inv.groupby("shop_id")["units_sold"].transform("size").mul(cfg["strong_top_share"]).apply(math.floor).clip(lower=1)

    for r in inv.itertuples():
        units, qty, reorder = float(r.units_sold), float(r.quantity_on_hand), float(r.reorder_level)
        unit = r.product_unit

        def U(n):
            return f"{_num(n)} x {unit}"

        daily = units / days
        cover = qty / daily if daily > 0 else None
        low_stock = qty <= reorder

        # 1. Low stock (recorded snapshot at or below the reorder level)
        if low_stock:
            if units >= cfg["min_units_for_quantity"]:
                urgent = qty <= 0 or (cover is not None and cover < cfg["urgent_cover_days"])
                priority = "high" if urgent else "medium"
                reason = (f"Recorded stock is {U(qty)}, at or below the reorder level of {_num(reorder)}. "
                          f"At the average rate over {period_label} ({_num(units)} sold) that is about {cover:.0f} day(s) of stock.")
                action = "Check today's supplier price and review restocking soon." if not urgent else \
                    "Review restocking today; check today's supplier price first."
            else:
                priority = "low"
                reason = (f"Recorded stock is {U(qty)}, at or below the reorder level of {_num(reorder)}, "
                          f"but only {_num(units)} sold in {period_label}, too little history to suggest a quantity.")
                action = "Decide whether you still want to stock this product before reordering."
            recs.append(_rec(r, "low_stock", priority, reason, action,
                             {"quantity_on_hand": qty, "reorder_level": reorder, "units_sold": units,
                              "stock_cover_days": None if cover is None else round(cover, 1)}))

            # 5. Replenishment suggestion (review only, no order is placed)
            if units >= cfg["min_units_for_quantity"]:
                need = int(max(0, math.ceil(daily * cfg["target_cover_days"]) - qty))
                if need > 0:
                    recs.append(_rec(
                        r, "replenishment", priority,
                        f"Selling about {daily:.1f} x {unit} a day on average in {period_label}; "
                        f"{cfg['target_cover_days']} days of that is {math.ceil(daily * cfg['target_cover_days'])} and you hold {_num(qty)}.",
                        f"Consider reviewing an order of about {U(need)}. This is an average-based estimate, "
                        f"not a forecast, and no order has been placed.",
                        {"suggested_review_quantity": need, "average_daily_units": round(daily, 2),
                         "target_cover_days": cfg["target_cover_days"]}))

        # 3. Low margin (from sales in the selected period)
        margin = r.gross_margin_pct
        if r.revenue_kes > 0 and margin is not None and not pd.isna(margin) and margin < cfg["low_margin_pct"]:
            recs.append(_rec(
                r, "low_margin", "medium",
                f"Gross margin is {margin:.1f}%, below the {cfg['low_margin_pct']:.0f}% demo threshold, "
                f"on KSh {r.revenue_kes:,.0f} revenue in {period_label}. {GROSS_PROFIT_NOTE}",
                "Compare your buying price and selling price, and check for supplier price changes.",
                {"gross_margin_pct": margin, "revenue_kes": round(float(r.revenue_kes), 2),
                 "gross_profit_kes": round(float(r.gross_profit_kes), 2),
                 "threshold_pct": cfg["low_margin_pct"]}))

        # 4. Slow-moving, from the supplied summary flag and its own threshold and window
        if not pd.isna(r.slow_flag) and bool(r.slow_flag):
            over = qty > cfg["overstock_multiple"] * reorder
            start = pd.Timestamp(r.slow_period_start).strftime("%Y-%m-%d") if not pd.isna(r.slow_period_start) else "?"
            end = pd.Timestamp(r.slow_period_end).strftime("%Y-%m-%d") if not pd.isna(r.slow_period_end) else "?"
            recs.append(_rec(
                r, "slow_moving", "medium" if over else "low",
                f"The supplied summary flags this as slow-moving: {_num(r.slow_units_90)} sold between {start} and {end} "
                f"(threshold: fewer than {_num(r.slow_threshold)}). You hold {U(qty)}."
                + (" That is more than double the reorder level." if over else ""),
                "Avoid restocking for now and consider a small promotion." if over else
                "Consider a small promotion or ordering less.",
                {"units_in_summary_window": float(r.slow_units_90), "threshold_units": float(r.slow_threshold),
                 "window_start": start, "window_end": end, "quantity_on_hand": qty}))

        # 2. Strong seller (rank by units sold in the selected period)
        if units >= cfg["strong_min_units"] and r.unit_rank <= r.strong_cutoff:
            recs.append(_rec(
                r, "strong_seller", "medium" if low_stock else "low",
                f"One of this shop's top {int(cfg['strong_top_share'] * 100)}% of products by units sold in {period_label}: "
                f"{U(units)} sold.",
                "Keep it well stocked." + (" It is currently at or below its reorder level." if low_stock else ""),
                {"units_sold": units, "rank_in_shop": int(r.unit_rank)}))

    recs.sort(key=lambda x: (PRIORITY_ORDER[x["priority"]], TYPE_ORDER[x["type"]], x["shop_id"], x["product"]))
    return recs


def summarise(recs: list[dict]) -> dict:
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for r in recs:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
    return {"total": len(recs), "by_type": by_type, "by_priority": by_priority}
