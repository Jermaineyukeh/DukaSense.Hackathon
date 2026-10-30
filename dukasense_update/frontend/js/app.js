/* DukaSense frontend. Every number shown comes from the Flask API; no calculations are done here. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { view: "dashboard", shop: "", start: "", end: "", sort: "product_name", order: "asc", token: 0, datesSet: false, loaded: false };
const charts = {};
const TYPE_LABELS = { low_stock: "Low stock", replenishment: "Replenishment", low_margin: "Low margin", slow_moving: "Slow-moving", strong_seller: "Strong seller" };
const PRIORITY_LABELS = { high: "High priority", medium: "Medium priority", low: "Low priority" };

/* ---------- formatting ---------- */
const nf0 = new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-KE", { maximumFractionDigits: 1 });
const ksh = (n) => (n == null ? "n/a" : "KSh " + nf0.format(n));
const pct = (n) => (n == null ? "n/a" : nf1.format(n) + "%");
const qty = (n) => (n == null ? "n/a" : nf1.format(n));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const niceDate = (iso) => (iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "n/a");
const sentence = (s) => { const t = String(s || "").toLowerCase(); return t.charAt(0).toUpperCase() + t.slice(1); };

/* ---------- api ---------- */
async function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => { if (v !== "" && v != null) url.searchParams.set(k, v); });
  let res;
  try { res = await fetch(url); } catch (e) { throw new Error("Cannot reach the DukaSense server. Start it again with python app.py."); }
  let body = null;
  try { body = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) {
    const err = new Error(body?.error?.message || `Request failed (${res.status})`);
    err.code = body?.error?.code;
    err.details = body?.error?.details;
    throw err;
  }
  return body;
}
async function post(path, body) {
  let res;
  try { res = await fetch(path, { method: "POST", body }); } catch (e) { throw new Error("Cannot reach the DukaSense server. Start it again with python app.py."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error?.message || `Request failed (${res.status})`);
    err.details = data?.error?.details;
    throw err;
  }
  return data;
}
const baseParams = () => ({ shop: state.shop, start: state.start, end: state.end });

/* ---------- ui helpers ---------- */
let toastTimer;
function toast(message, kind = "ok", details = null, sticky = false) {
  const el = $("toast");
  el.className = "toast " + kind;
  el.innerHTML = esc(message) + (details?.length ? "<ul>" + details.map((d) => `<li>${esc(d)}</li>`).join("") + "</ul>" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  if (!sticky && kind === "ok") toastTimer = setTimeout(() => (el.hidden = true), 7000);
}
const setLoading = (on) => { $("loading").hidden = !on; };
const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function showWelcome() {
  state.loaded = false;
  $("welcome").hidden = false;
  $("app").hidden = true; $("shell").classList.add("is-empty");
  $("dataTag").hidden = true; $("bellDot").hidden = true; closeDrawer();
  Object.keys(charts).forEach((k) => { charts[k].destroy(); delete charts[k]; });
}
function showApp() {
  state.loaded = true;
  $("welcome").hidden = true; $("app").hidden = false; $("shell").classList.remove("is-empty");
  $("dataTag").hidden = false;
}
function resetFilters() {
  state.shop = ""; state.start = state.end = ""; state.datesSet = false;
  $("startDate").value = $("endDate").value = "";
  $("categoryFilter").length = 1;
  $("searchInput").value = ""; $("globalSearch").value = ""; $("statusFilter").value = ""; $("slowFilter").checked = false;
}

/* ---------- charts ---------- */
function drawChart(id, config) {
  const canvas = $(id);
  if (charts[id]) charts[id].destroy();
  if (typeof Chart === "undefined") {
    canvas.parentElement.innerHTML = empty("Chart.js could not be loaded. Check your internet connection and refresh.");
    return;
  }
  Chart.defaults.color = cssVar("--text-dim");
  Chart.defaults.borderColor = cssVar("--grid");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  charts[id] = new Chart(canvas, config);
}
function ensureCanvas(id) {
  if (!$(id)) document.querySelectorAll(".chart-box").forEach((b) => { if (b.dataset.id === id) b.innerHTML = `<canvas id="${id}"></canvas>`; });
}
function chartOrEmpty(id, rows, builder, message = "No sales in this selection.") {
  ensureCanvas(id);
  const canvas = $(id);
  if (!canvas) return;
  const box = canvas.parentElement;
  box.dataset.id = id;
  if (!rows || !rows.length) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    box.innerHTML = empty(message);
    return;
  }
  builder();
}
const money = { callback: (v) => "KSh " + nf0.format(v) };
const tooltipKsh = { callbacks: { label: (c) => `${c.dataset.label}: KSh ${nf0.format(c.parsed.y ?? c.parsed.x)}` } };
const barOptions = (extra = {}) => ({ responsive: true, maintainAspectRatio: false, ...extra });

/* ---------- renderers ---------- */
function renderMeta(meta) {
  $("shopName").textContent = meta.shop_name;
  $("periodLine").textContent = `Sales from ${niceDate(meta.period.start)} to ${niceDate(meta.period.end)}. Stock as at ${niceDate(meta.inventory_snapshot_date)}.`;
  $("dataTag").textContent = sentence(meta.data_label || "Synthetic demo data");
  state.meta = meta;
  const info = [
    ["File", `${meta.filename} (${meta.source === "upload" ? "uploaded" : "sample"})`],
    ["Label", sentence(meta.data_label || "Synthetic demo data")],
    ["Sales period", `${niceDate(meta.period.dataset_start)} to ${niceDate(meta.period.dataset_end)}`],
    ["Stock snapshot", niceDate(meta.inventory_snapshot_date)],
    ["Selected view", `${meta.shop_name}, ${niceDate(meta.period.start)} to ${niceDate(meta.period.end)} (${meta.period.days} days)`],
  ];
  $("dataInfo").innerHTML = info.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") +
    "<dt>Notes</dt><dd>" + meta.notes.map(esc).join("<br>") + "</dd>";
  if (!state.datesSet) {
    ["startDate", "endDate"].forEach((id) => { $(id).min = meta.period.dataset_start; $(id).max = meta.period.dataset_end; });
    state.datesSet = true;
  }
}

function renderKpis(k) {
  const cells = [
    ["Revenue", ksh(k.revenue_kes), `${nf0.format(k.sale_lines)} sale lines`],
    ["Gross profit", ksh(k.gross_profit_kes), "Revenue minus estimated cost of goods, before expenses"],
    ["Gross margin", pct(k.gross_margin_pct), "Gross profit as a share of revenue"],
    ["Units sold", nf0.format(k.units_sold), k.transactions != null ? `${nf0.format(k.transactions)} transactions` : ""],
  ];
  $("kpis").innerHTML = cells.map(([l, v, s], i) => `<div class="stat glass glass-hover enter" style="--i:${i}"><div class="k">${esc(l)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join("");
}

function renderDashboardCharts(d) {
  const pts = d.trend.points;
  $("trendNote").textContent = d.trend.granularity === "month" ? "By month" : "By day";
  chartOrEmpty("trendChart", pts, () => drawChart("trendChart", {
    type: "line",
    data: { labels: pts.map((p) => p.period), datasets: [
      { label: "Revenue", data: pts.map((p) => p.revenue_kes), borderColor: cssVar("--chart-1"), backgroundColor: cssVar("--chart-1"), tension: .25, borderWidth: 2.5, pointRadius: pts.length > 40 ? 0 : 3 },
      { label: "Gross profit", data: pts.map((p) => p.gross_profit_kes), borderColor: cssVar("--chart-2"), backgroundColor: cssVar("--chart-2"), tension: .25, borderWidth: 2.5, pointRadius: pts.length > 40 ? 0 : 3 },
    ] },
    options: barOptions({ interaction: { mode: "index", intersect: false }, scales: { y: { ticks: money, beginAtZero: true }, x: { ticks: { maxTicksLimit: 12 } } }, plugins: { tooltip: tooltipKsh } }),
  }));

  const cats = d.categories;
  chartOrEmpty("categoryChart", cats, () => drawChart("categoryChart", {
    type: "bar",
    data: { labels: cats.map((c) => c.product_category), datasets: [{ label: "Revenue", data: cats.map((c) => c.revenue_kes), backgroundColor: cssVar("--chart-1"), borderRadius: 3 }] },
    options: barOptions({ indexAxis: "y", scales: { x: { ticks: money, beginAtZero: true } }, plugins: { legend: { display: false }, tooltip: tooltipKsh } }),
  }));
}

function renderAnalytics(d) {
  const tp = d.top_products;
  const metric = $("productSort").value;
  const metricLabel = { revenue_kes: "Revenue", units_sold: "Units sold", gross_profit_kes: "Gross profit" }[metric];
  chartOrEmpty("productChart", tp, () => drawChart("productChart", {
    type: "bar",
    data: { labels: tp.map((p) => p.product_name), datasets: [{ label: metricLabel, data: tp.map((p) => p[metric]), backgroundColor: cssVar("--chart-1"), borderRadius: 3 }] },
    options: barOptions({ indexAxis: "y", scales: { x: { ticks: metric === "units_sold" ? {} : money, beginAtZero: true } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => metric === "units_sold" ? `${nf0.format(c.parsed.x)} units` : `KSh ${nf0.format(c.parsed.x)}` } } } }),
  }));

  const cats = d.categories;
  chartOrEmpty("marginChart", cats, () => drawChart("marginChart", {
    type: "bar",
    data: { labels: cats.map((c) => c.product_category), datasets: [{ label: "Gross margin", data: cats.map((c) => c.gross_margin_pct), backgroundColor: cssVar("--chart-2"), borderRadius: 3 }] },
    options: barOptions({ indexAxis: "y", scales: { x: { ticks: { callback: (v) => v + "%" }, beginAtZero: true } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => pct(c.parsed.x) } } } }),
  }));

  const shops = d.shop_comparison;
  chartOrEmpty("shopChart", shops, () => drawChart("shopChart", {
    type: "bar",
    data: { labels: shops.map((s) => s.shop_name), datasets: [
      { label: "Revenue", data: shops.map((s) => s.revenue_kes), backgroundColor: cssVar("--chart-1"), borderRadius: 3 },
      { label: "Gross profit", data: shops.map((s) => s.gross_profit_kes), backgroundColor: cssVar("--chart-2"), borderRadius: 3 },
    ] },
    options: barOptions({ scales: { y: { ticks: money, beginAtZero: true } }, plugins: { tooltip: tooltipKsh } }),
  }), "No shop sales in this date range.");
  renderShopTable(shops);

  $("perfTable").innerHTML = tp.length
    ? `<thead><tr><th>Product</th><th>Category</th><th>Unit</th><th class="num">Units sold</th><th class="num">Revenue</th><th class="num">Gross profit</th><th class="num">Margin</th></tr></thead><tbody>` +
      tp.map((p) => `<tr><td>${esc(p.product_name)}</td><td>${esc(p.product_category)}</td><td>${esc(p.product_unit)}</td><td class="num">${nf0.format(p.units_sold)}</td><td class="num">${ksh(p.revenue_kes)}</td><td class="num">${ksh(p.gross_profit_kes)}</td><td class="num">${pct(p.gross_margin_pct)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td>${empty("No sales in this selection.")}</td></tr></tbody>`;
}

function renderShopTable(shops) {
  $("shopTable").innerHTML = shops.length
    ? `<thead><tr><th>Shop</th><th>County</th><th class="num">Units sold</th><th class="num">Revenue</th><th class="num">Gross profit</th><th class="num">Margin</th></tr></thead><tbody>` +
      shops.map((s) => `<tr class="pick ${s.shop_id === state.shop ? "current" : ""}" data-shop="${esc(s.shop_id)}"><td>${esc(s.shop_name)}</td><td>${esc(s.county ?? "n/a")}</td><td class="num">${s.units_sold == null ? "n/a" : nf0.format(s.units_sold)}</td><td class="num">${ksh(s.revenue_kes)}</td><td class="num">${ksh(s.gross_profit_kes)}</td><td class="num">${pct(s.gross_margin_pct)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td>${empty("No sales in this selection.")}</td></tr></tbody>`;
}

function recCard(r, showShop) {
  const metrics = r.metrics ? Object.entries(r.metrics).filter(([, v]) => v != null).map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "number" ? nf1.format(v) : v}`).join(", ") : "";
  const tone = { high: "bad", medium: "warn", low: "info" }[r.priority] || "info";
  return `<article class="rec glass glass-hover ${esc(r.priority)}">
    <div class="rec-top"><span>${esc(TYPE_LABELS[r.type] || r.type)}</span><span class="badge" data-tone="${tone}">${esc(PRIORITY_LABELS[r.priority] || r.priority)}</span></div>
    <h3>${esc(r.product)}</h3>
    ${showShop ? `<div class="shop">${esc(r.shop_name)} (${esc(r.shop_id)})</div>` : ""}
    <p>${esc(r.reason)}</p>
    <p class="action"><strong>Next step:</strong> ${esc(r.suggested_action)}</p>
    <details><summary>The numbers behind this</summary><div>Confidence: ${esc(r.confidence.toLowerCase())}, based on sales in the selected period. ${esc(metrics)}</div></details>
  </article>`;
}

function renderTopRecs(d) {
  const high = d.recommendations.filter((r) => r.priority === "high");
  const list = (high.length ? high : d.recommendations).slice(0, 6);
  $("topRecs").innerHTML = list.length ? list.map((r) => recCard(r, !state.shop)).join("") : empty("Nothing needs attention for this selection.");
}

function renderRecommendations(d) {
  const s = d.summary;
  $("navRecCount").textContent = s.total || "";
  const typeBits = Object.entries(s.by_type).map(([t, n]) => `${TYPE_LABELS[t] || t}: ${n}`).join(", ");
  $("recSummary").textContent = s.total
    ? `${s.total} recommendations for ${d.meta.shop_name}. ${typeBits}. The low margin line is ${d.thresholds.low_margin_pct}% (a demo setting). These come from simple rules, not a forecast, and no orders are placed.`
    : "No recommendations for this selection.";
  $("recList").innerHTML = d.recommendations.length
    ? d.recommendations.map((r) => recCard(r, !state.shop)).join("")
    : empty("Nothing to show. Try another shop, a wider date range or clear the filters.");
}

function renderProducts(d) {
  const showShop = !state.shop;
  $("invSummary").textContent = `${d.count} products shown, ${d.reorder_count} at or below their reorder level. Stock is a snapshot from ${niceDate(d.meta.inventory_snapshot_date)} and does not change with the dates. Units sold and margin follow the selected dates.`;
  const sel = $("categoryFilter");
  if (sel.options.length <= 1) d.categories.forEach((c) => sel.add(new Option(c, c)));
  const cols = [
    ...(showShop ? [["shop_name", "Shop", false]] : []),
    ["product_name", "Product", false], ["product_category", "Category", false], ["product_unit", "Unit", false],
    ["quantity_on_hand", "In stock", true], ["reorder_level", "Reorder level", true], ["stock_status", "Status", false],
    ["stock_cover_days", "Days of stock", true], ["units_sold", "Units sold", true], ["gross_margin_pct", "Margin", true],
  ];
  const arrow = (k) => (state.sort === k ? (state.order === "asc" ? " ▲" : " ▼") : "");
  const head = `<thead><tr>${cols.map(([k, l, n]) => `<th class="sortable ${n ? "num" : ""}" data-sort="${k}" tabindex="0" role="button" aria-label="Sort by ${l}">${l}${arrow(k)}</th>`).join("")}</tr></thead>`;
  if (!d.products.length) { $("productTable").innerHTML = head + `<tbody><tr><td colspan="${cols.length}">${empty("No products match these filters.")}</td></tr></tbody>`; return; }
  const body = d.products.map((p) => `<tr>
    ${showShop ? `<td>${esc(p.shop_name)}</td>` : ""}
    <td>${esc(p.product_name)}${p.slow_flag ? '<span class="slow-tag">Slow-moving</span>' : ""}</td>
    <td>${esc(p.product_category)}</td><td>${esc(p.product_unit)}</td>
    <td class="num">${qty(p.quantity_on_hand)}</td><td class="num">${qty(p.reorder_level)}</td>
    <td><span class="badge" data-tone="${p.stock_status === "REORDER" ? "warn" : "ok"}">${p.stock_status === "REORDER" ? "Reorder" : "Fine"}</span></td>
    <td class="num">${p.stock_cover_days == null ? "n/a" : qty(p.stock_cover_days)}</td>
    <td class="num">${qty(p.units_sold)}</td><td class="num">${pct(p.gross_margin_pct)}</td></tr>`).join("");
  $("productTable").innerHTML = head + "<tbody>" + body + "</tbody>";
}

/* ---------- data loading ---------- */
async function loadProducts() {
  const params = { ...baseParams(), search: $("searchInput").value.trim(), status: $("statusFilter").value, category: $("categoryFilter").value, slow: $("slowFilter").checked ? "1" : "", sort: state.sort, order: state.order };
  try { renderProducts(await api("/api/products", params)); }
  catch (e) { if (e.code === "no_data") return showWelcome(); $("productTable").innerHTML = `<tbody><tr><td>${empty(e.message)}</td></tr></tbody>`; }
}
function miniTable(id, rows, showShop, extra) {
  const cols = [...(showShop ? ["Shop"] : []), "Product", "Category", "In stock", "Reorder level", ...extra.map((e) => e[0])];
  const first = (showShop ? 1 : 0) + 2;
  const head = `<thead><tr>${cols.map((c, i) => `<th class="${i >= first ? "num" : ""}">${c}</th>`).join("")}</tr></thead>`;
  if (!rows.length) { $(id).innerHTML = head + `<tbody><tr><td colspan="${cols.length}">${empty("Nothing here for this selection.")}</td></tr></tbody>`; return; }
  $(id).innerHTML = head + "<tbody>" + rows.map((p) => `<tr>${showShop ? `<td>${esc(p.shop_name)}</td>` : ""}<td>${esc(p.product_name)}</td><td>${esc(p.product_category)}</td><td class="num">${qty(p.quantity_on_hand)} × ${esc(p.product_unit)}</td><td class="num">${qty(p.reorder_level)}</td>${extra.map((e) => `<td class="num">${e[1](p)}</td>`).join("")}</tr>`).join("") + "</tbody>";
}
async function loadStockLists() {
  const base = baseParams();
  const [a, sl, all] = await Promise.all([
    api("/api/products", { ...base, status: "REORDER", sort: "stock_cover_days" }),
    api("/api/products", { ...base, slow: "1", sort: "units_sold" }),
    api("/api/products", { ...base }),
  ]);
  const show = !state.shop;
  $("alertSummary").textContent = `${a.count} products are at or below their reorder level. Stock is a snapshot from ${niceDate(a.meta.inventory_snapshot_date)}.`;
  miniTable("alertTable", a.products, show, [["Days of stock", (p) => (p.stock_cover_days == null ? "n/a" : qty(p.stock_cover_days))], ["Units sold", (p) => qty(p.units_sold)]]);
  $("slowSummary").textContent = `${sl.count} products are flagged slow-moving in the uploaded data.`;
  miniTable("slowTable", sl.products, show, [["Units sold", (p) => qty(p.units_sold)], ["Margin", (p) => pct(p.gross_margin_pct)]]);
  const setBadge = (id, n) => { $(id).textContent = n || ""; };
  setBadge("navAlertCount", a.count); setBadge("navSlowCount", sl.count);
  $("bellDot").textContent = a.count; $("bellDot").hidden = !a.count;
  void all;
}
async function loadRecommendations(updateTop = true) {
  const params = { ...baseParams(), type: $("recType").value, priority: $("recPriority").value };
  const d = await api("/api/recommendations", params);
  renderRecommendations(d);
  if (updateTop) renderTopRecs(params.type || params.priority ? await api("/api/recommendations", baseParams()) : d);
}
async function refreshAll() {
  const token = ++state.token;
  setLoading(true);
  try {
    const dash = await api("/api/dashboard", { ...baseParams(), sort: $("productSort").value });
    if (token !== state.token) return;
    renderMeta(dash.meta); renderKpis(dash.kpis); renderDashboardCharts(dash); renderAnalytics(dash);
    await Promise.all([loadRecommendations(), loadProducts(), loadStockLists()]);
  } catch (e) {
    if (e.code === "no_data") { showWelcome(); return; }
    toast(e.message, "err", null, true);
    $("kpis").innerHTML = empty("Could not load data. " + e.message);
  } finally { if (token === state.token) setLoading(false); }
}
async function loadShops() {
  const { shops } = await api("/api/shops");
  const sel = $("shopSelect");
  sel.innerHTML = '<option value="">All shops</option>' + shops.map((s) => `<option value="${esc(s.shop_id)}">${esc(s.shop_name)}${s.county ? " (" + esc(s.county) + ")" : ""}</option>`).join("");
  sel.value = "";
  state.shop = "";
}
async function dataArrived(message) {
  resetFilters();
  showApp();
  showView("dashboard");
  await loadShops();
  await refreshAll();
  if (message) toast(message, "ok");
}

/* ---------- upload, sample and clear ---------- */
async function uploadFile(file) {
  if (!file) return;
  setLoading(true);
  const form = new FormData(); form.append("file", file);
  try {
    const body = await post("/api/upload", form);
    await dataArrived(`${body.filename} is loaded: ${nf0.format(body.rows.sales)} sales rows from ${body.shops} shop(s). It stays in memory until you restart the app.`);
  } catch (e) {
    toast(e.message, "err", e.details, true);
  } finally { $("uploadInput").value = ""; setLoading(false); }
}
async function loadSample() {
  setLoading(true);
  try { const body = await post("/api/sample"); await dataArrived(body.message); }
  catch (e) { toast(e.message, "err", null, true); }
  finally { setLoading(false); }
}
async function clearData() {
  try { await post("/api/unload"); showWelcome(); $("toast").hidden = true; }
  catch (e) { toast(e.message, "err", null, true); }
}

$("uploadInput").addEventListener("change", (e) => uploadFile(e.target.files[0]));
$("replaceBtn").addEventListener("click", () => $("uploadInput").click());
$("sampleBtn").addEventListener("click", loadSample);
$("sampleBtn2").addEventListener("click", loadSample);
$("clearBtn").addEventListener("click", clearData);
$("clearBtn2").addEventListener("click", clearData);
$("uploadBtn2").addEventListener("click", () => $("uploadInput").click());
const drop = $("dropZone");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

/* ---------- navigation and filters ---------- */
let searchTimer;
function showView(v) {
  state.view = v;
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + v));
  document.querySelectorAll(".tab").forEach((b) => { if (b.dataset.view === v) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  moveIndicator();
  closeDrawer();
  window.scrollTo({ top: 0 });
  Object.values(charts).forEach((c) => c.resize());
}
function moveIndicator() {
  const ind = $("navIndicator");
  const cur = document.querySelector(".tab[aria-current='page']");
  if (!cur || !cur.offsetHeight) { ind.classList.remove("ready"); return; }
  ind.style.height = cur.offsetHeight + "px";
  ind.style.transform = `translateY(${cur.offsetTop}px)`;
  ind.classList.add("ready");
}
window.addEventListener("resize", moveIndicator);
function closeDrawer() { $("sidebar").classList.remove("open"); $("scrim").classList.remove("open"); }
$("menuBtn").addEventListener("click", () => { $("sidebar").classList.add("open"); $("scrim").classList.add("open"); });
$("scrim").addEventListener("click", closeDrawer);
$("bellBtn").addEventListener("click", () => showView("alerts"));
$("globalSearch").addEventListener("input", (e) => { $("searchInput").value = e.target.value; showView("inventory"); clearTimeout(searchTimer); searchTimer = setTimeout(loadProducts, 250); });
$("searchInput").addEventListener("input", (e) => { $("globalSearch").value = e.target.value; });
document.addEventListener("keydown", (e) => { if (e.key === "/" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) && state.loaded) { e.preventDefault(); $("globalSearch").focus(); } });
document.addEventListener("pointermove", (e) => { const el = e.target.closest?.(".glass-hover"); if (!el) return; const r = el.getBoundingClientRect(); el.style.setProperty("--mx", (e.clientX - r.left) + "px"); el.style.setProperty("--my", (e.clientY - r.top) + "px"); });
$("shopTable").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-shop]"); if (!tr) return; state.shop = tr.dataset.shop; $("shopSelect").value = state.shop; refreshAll(); });
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.goto)));

$("shopSelect").addEventListener("change", (e) => { state.shop = e.target.value; refreshAll(); });
$("startDate").addEventListener("change", (e) => { state.start = e.target.value; refreshAll(); });
$("endDate").addEventListener("change", (e) => { state.end = e.target.value; refreshAll(); });
$("resetDates").addEventListener("click", () => { state.start = state.end = ""; $("startDate").value = $("endDate").value = ""; refreshAll(); });
$("refreshBtn").addEventListener("click", refreshAll);
$("productSort").addEventListener("change", refreshAll);
$("recType").addEventListener("change", () => loadRecommendations(false).catch((e) => toast(e.message, "err")));
$("recPriority").addEventListener("change", () => loadRecommendations(false).catch((e) => toast(e.message, "err")));

$("searchInput").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadProducts, 250); });
["statusFilter", "categoryFilter", "slowFilter"].forEach((id) => $(id).addEventListener("change", loadProducts));
function sortBy(k) { state.order = state.sort === k && state.order === "asc" ? "desc" : "asc"; state.sort = k; loadProducts(); }
$("productTable").addEventListener("click", (e) => { const th = e.target.closest("th[data-sort]"); if (th) sortBy(th.dataset.sort); });
$("productTable").addEventListener("keydown", (e) => { const th = e.target.closest("th[data-sort]"); if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(th.dataset.sort); } });

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $("themeBtn").setAttribute("aria-label", t === "dark" ? "Switch to light mode" : "Switch to dark mode");
  try { localStorage.setItem("dukasense-theme", t); } catch (e) { /* storage unavailable */ }
}
$("themeBtn").addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  if (state.loaded) refreshAll();
});

/* ---------- start ---------- */
(async function init() {
  let saved = null;
  try { saved = localStorage.getItem("dukasense-theme"); } catch (e) { /* ignore */ }
  setTheme(saved === "light" ? "light" : "dark");
  try {
    const health = await api("/api/health");
    if (health.dataset_loaded) { showApp(); showView("dashboard"); await loadShops(); await refreshAll(); }
    else showWelcome();
  } catch (e) { showWelcome(); toast(e.message, "err", null, true); }
})();
