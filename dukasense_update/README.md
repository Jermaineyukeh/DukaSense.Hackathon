# DukaSense

Retail analytics demo for small Kenyan shops. Flask + Pandas backend, vanilla JS + Chart.js frontend.

**All data is synthetic and historical (Jan to Dec 2025). Stock levels come from one inventory snapshot dated 2025-12-31.
Recommendations are simple rules, not a trained model or a forecast. Gross profit is revenue minus estimated COGS, not net profit.**

## Setup and run

```bash
cd dukasense
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. The app starts empty. Upload `DukaSense_Combined_Synthetic_Dataset.csv` (or click "Use the sample data") and the dashboard fills in.
Chart.js and the fonts load from CDNs, so the browser needs internet access. To preload the sample file at startup instead, set `DUKASENSE_AUTOLOAD=1` before running.

## Tests

```bash
python -m pytest -q
```

## Files

| File | Purpose |
|---|---|
| `app.py` | Routes, startup load of the CSV, upload and restore |
| `analytics.py` | Validation, record-type filtering, KPIs, trends, inventory view |
| `recommendations.py` | Rule-based recommendations and thresholds (`CONFIG`) |
| `frontend/` | `index.html`, `css/style.css`, `js/app.js` |
| `tests/` | Calculations, record types, filters, rules, CSV validation |
| `DukaSense_Combined_Synthetic_Dataset.csv` | Demo data (28,683 rows, 5 record types, 12 shops) |

## API

All endpoints return JSON. Errors look like `{"error": {"code", "message", "details"?}}`.
Shop and date filters: `shop` (e.g. `SH001`, omit for all), `start`, `end` (`YYYY-MM-DD`).

- `GET /api/health`: whether a dataset is loaded, its source and row counts per record type. Other data endpoints return 503 `no_data` until a CSV is loaded
- `GET /api/shops`
- `GET /api/dashboard`: KPIs, trend, categories, top products (`sort`, `limit`), shop comparison
- `GET /api/products`: stock table; adds `search`, `status` (OK or REORDER), `category`, `slow=1`, `sort`, `order`
- `GET /api/recommendations`: adds `type`, `priority`
- `POST /api/upload`: multipart field `file`, a `.csv` in the combined format
- `POST /api/sample` (also `/api/restore`): load the bundled sample CSV
- `POST /api/unload`: clear the dataset and return to the empty start screen

## Recommendation rules

| Type | Rule |
|---|---|
| Low stock | Snapshot stock at or below reorder level. High priority if under 7 days of stock at the period's average sales rate |
| Replenishment | For low stock products with at least 5 units sold in the period: review ordering enough for 14 days of average sales, minus stock. No order is placed |
| Strong seller | Top 20% of a shop's products by units sold in the selected period, at least 5 units |
| Low margin | Period gross margin below 10% (demo threshold, change `low_margin_pct`) |
| Slow-moving | Uses the supplied `SLOW_MOVING_SUMMARY` flag and its own threshold and window (3 Oct to 31 Dec 2025) |

Low stock and slow-moving use the snapshot and the supplied summary, so they do not move with the date filter.
Strong seller, low margin and replenishment quantities use sales in the selected period.

## Upload rules and limits

The upload must be a UTF-8 CSV with the combined schema: a `record_type` column, SALE, PRODUCT_CATALOGUE and INVENTORY_SNAPSHOT rows,
their required columns, numeric sales values that match quantity x price/cost, valid dates and no duplicate sale lines.
Rejected files list every problem and leave the active data unchanged. Maximum size 25 MB.

This is an unauthenticated single-instance demo. An accepted upload replaces the in-memory dataset for everyone using the instance,
is not saved to disk and resets on restart. Use "Restore demo data" (or `POST /api/restore`) to go back.
Arbitrary spreadsheets and permanent storage are not supported.
