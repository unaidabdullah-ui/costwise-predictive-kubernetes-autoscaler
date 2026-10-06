// CostWise control room. Plain ES module, no external libraries, no inline styles (strict CSP).
// Every number shown comes from /api/dashboard (Prometheus, Kubernetes, the LSTM and the controller).

import { drawChart } from "./chart.js";

const view = document.getElementById("view");
const pillsEl = document.getElementById("pills");
const toastEl = document.getElementById("toast");
const POLL_MS = 2000;

let data = null;
let page = null;
let pollTimer = null;
let lastOk = 0;

// ------------------------------------------------------------------ helpers
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const f1 = (v, d = 1) => (isNum(v) ? v.toFixed(d) : "—");
const int = (v) => (isNum(v) ? Math.round(v).toLocaleString("en-IN") : "—");
const money = (v, sym = "₹", d = 2) => (isNum(v) ? `${sym}${v.toFixed(d)}` : "—");
const hhmmss = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });
const $ = (id) => document.getElementById(id);
function setText(id, text) { const el = $(id); if (el && el.textContent !== text) el.textContent = text; }

const ICON = {
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><circle cx="17" cy="9" r="2.6"/><path d="M16 14.6c2.7.2 4.6 1.9 5.3 4.9"/></svg>',
  traffic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5-6 4 3 6-8"/><path d="M14 6h4v4"/></svg>',
  forecast: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 18c3 0 4-8 7-8s3 4 5 4" /><path d="M15 14c2 0 3-5 6-6" stroke-dasharray="2.5 2.5"/></svg>',
  pods: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z"/><path d="M12 12l8-4.6M12 12v9.2M12 12L4 7.4"/></svg>',
  target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>',
  cost: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 4h12M6 9h12M9 4c5 0 5 9 0 9H6l9 7"/></svg>',
  budget: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10h18"/><circle cx="16.5" cy="14.5" r="1.4" fill="currentColor"/></svg>',
  status: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/></svg>',
  guard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  k8s: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 2.5l8.5 4.2 2 9.2-5.9 7.3H7.4l-5.9-7.3 2-9.2z"/><circle cx="12" cy="12.5" r="3"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.1"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5h4"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l12.5-7.5z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.5 2L4 13.5h6.5L9.5 22 20 9.5h-6.5z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  reset: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3V4zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3V4z"/></svg>',
  shop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 5h3l2.2 10h10.3L21 8H7"/><circle cx="9.5" cy="19" r="1.4" fill="currentColor"/><circle cx="17" cy="19" r="1.4" fill="currentColor"/></svg>',
};
const STATUS_SYMBOL = { good: "✓", warning: "!", critical: "✕", unknown: "?" };
const STATUS_WORD = { good: "OK", warning: "Attention", critical: "Problem", unknown: "Checking" };

function toast(message, hint = "", isError = false) {
  toastEl.innerHTML = `<div>${esc(message)}</div>${hint ? `<div class="t-hint">${esc(hint)}</div>` : ""}`;
  toastEl.classList.toggle("err", isError);
  toastEl.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => toastEl.classList.remove("show"), isError ? 7000 : 3200);
}

async function getJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function post(path, body = {}) {
  const res = await fetch(path, {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-CostWise": "1" },
    body: JSON.stringify(body),
  });
  let out = {};
  try { out = await res.json(); } catch { /* ignore */ }
  if (!res.ok || out.ok === false) {
    const err = new Error(out.error || `HTTP ${res.status}`);
    err.hint = out.hint || "";
    throw err;
  }
  return out;
}

// ------------------------------------------------------------------ header pills
const PILL_ORDER = [["marketplace", "Marketplace"], ["kubernetes", "Kubernetes"], ["prometheus", "Prometheus"], ["model", "Forecast model"], ["traffic", "Traffic"], ["controller", "Controller"]];
function renderPills(health) {
  if (!health) {
    pillsEl.innerHTML = '<span class="pill critical"><span class="dot">✕</span>Dashboard server not reachable</span>';
    return;
  }
  pillsEl.innerHTML = PILL_ORDER.map(([key, label]) => {
    const c = health.components[key] || { status: "unknown", message: "" };
    return `<span class="pill ${c.status}" title="${esc(c.message)}${c.hint ? " — " + esc(c.hint) : ""}">
      <span class="dot" aria-hidden="true">${STATUS_SYMBOL[c.status] || "?"}</span>${label}
      <span class="state">${STATUS_WORD[c.status] || c.status}</span></span>`;
  }).join("");
}

// ------------------------------------------------------------------ dashboard page
const CONTROL_BUTTONS = [
  ["start", "START DEMO", "play", "primary", "Start normal shopping traffic (~170 shoppers)"],
  ["users", "GENERATE USERS", "plus", "", "Add 300 shoppers"],
  ["busy", "BUSY TRAFFIC", "traffic", "", "Gradually rise to ~900 shoppers"],
  ["flash", "FLASH SALE", "bolt", "hot", "Announce a flash sale: rush to ~1,600 shoppers"],
  ["stop", "STOP TRAFFIC", "stop", "", "All shoppers leave"],
  ["reset", "RESET DEMO", "reset", "danger", "Stop traffic, clear charts, back to the minimum pods"],
  ["retrain", "RETRAIN MODEL", "brain", "", "Retrain the LSTM on bootstrap + measured data"],
];
const ACTIONS = {
  start: () => post("/demo/start"),
  users: () => post("/demo/users"),
  busy: () => post("/demo/traffic", { scenario: "busy" }),
  flash: () => post("/demo/flash-sale"),
  stop: () => post("/demo/stop"),
  reset: () => post("/demo/reset"),
  retrain: () => post("/model/retrain"),
};
const ACTION_DONE = {
  start: "Demo started — normal traffic", users: "300 more shoppers are arriving", busy: "Busy traffic: shoppers rising to ~900",
  flash: "Flash sale announced — watch the forecast rise before the traffic does", stop: "Traffic stopped",
  reset: "Demo reset", retrain: "Retraining started (runs in the background)",
};

function metricCard(id, icon, label, extraClass = "") {
  return `<div class="card metric ${extraClass}" id="m-${id}"><div class="label">${ICON[icon]}${label}</div>
    <div class="value num" id="m-${id}-v">—</div><div class="sub" id="m-${id}-s">&nbsp;</div></div>`;
}

function dashboardSkeleton() {
  view.innerHTML = `
  <section class="controls" aria-label="Demo controls">
    ${CONTROL_BUTTONS.map(([id, label, icon, cls, title]) => `<button class="btn ${cls}" data-action="${id}" title="${esc(title)}">${ICON[icon]}${label}</button>`).join("")}
    <div class="scenario-note" id="scenario-note" aria-live="polite"></div>
  </section>
  <div id="problems"></div>
  <section class="cards" aria-label="Key numbers">
    ${metricCard("users", "users", "Simulated users")}
    ${metricCard("rps", "traffic", "Requests / sec", "actual")}
    ${metricCard("pred", "forecast", "Predicted load", "forecast")}
    ${metricCard("pods", "pods", "Current pods")}
    ${metricCard("req", "target", "Required pods")}
    ${metricCard("cost", "cost", "Est. cost / hour")}
    ${metricCard("budget", "budget", "Budget / hour")}
    ${metricCard("status", "status", "System status")}
  </section>
  <section class="row main">
    <div class="card">
      <div class="panel-title"><h2>Traffic: actual vs LSTM forecast</h2><span class="hint" id="chart-hint"></span></div>
      <div class="chart-wrap"><svg id="chart" role="img" aria-label="Chart of measured requests per second and the LSTM forecast"></svg>
        <div class="chart-empty" id="chart-empty"></div></div>
      <div class="legend">
        <span><i class="actual-line"></i>Actual (measured by Prometheus)</span>
        <span><i class="dash forecast-line"></i>LSTM forecast, made 30 s earlier</span>
        <span><i class="dot forecast-line"></i>LSTM forecast for the next 30 s</span>
        <span><i class="capacity-line"></i>Capacity of ready pods (at target load)</span>
      </div>
    </div>
    <div class="card" aria-live="polite">
      <div class="panel-title"><h2>Why did the system scale?</h2><span class="hint" id="why-time"></span></div>
      <div id="why"></div>
    </div>
  </section>
  <section class="row second">
    <div class="card"><div class="panel-title"><h2>Kubernetes pods</h2><span class="hint" id="pods-hint"></span></div><div class="pods" id="pods"></div><div class="pods-foot" id="pods-foot"></div></div>
    <div class="card"><div class="panel-title"><h2>Cost</h2><span class="hint">simulated prices</span></div><div id="cost"></div></div>
    <div class="card"><div class="panel-title"><h2>Shoppers</h2><span class="hint" id="users-hint"></span></div><div id="users"></div></div>
    <div class="card"><div class="panel-title"><h2>Reactive vs predictive</h2><span class="hint">measured</span></div><div id="cmp"></div></div>
  </section>
  <section class="row third">
    <div class="card"><div class="panel-title"><h2>What is happening</h2><span class="hint">event timeline</span></div><ul class="timeline" id="timeline"></ul></div>
    <div class="card hide-present"><div class="panel-title"><h2>Safeguards</h2><span class="hint">always on</span></div><div id="guards"></div></div>
  </section>`;
  view.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => runAction(btn)));
  // legend swatch colours via CSSOM (CSP forbids inline style attributes)
  view.querySelectorAll(".actual-line").forEach((el) => { el.style.borderTopColor = "var(--actual)"; });
  view.querySelectorAll(".forecast-line").forEach((el) => { el.style.borderTopColor = "var(--forecast)"; });
  view.querySelectorAll(".capacity-line").forEach((el) => { el.style.borderTopColor = "var(--capacity)"; });
}

async function runAction(btn) {
  const id = btn.dataset.action;
  if (id === "reset" && !confirm("Reset the demo? Traffic stops, charts and timeline are cleared and the store goes back to the minimum number of pods.")) return;
  btn.disabled = true;
  try {
    await ACTIONS[id]();
    toast(ACTION_DONE[id]);
    poll();
  } catch (err) {
    toast(`Could not ${btn.textContent.trim().toLowerCase()}: ${err.message}`, err.hint, true);
  } finally {
    setTimeout(() => { btn.disabled = false; }, 600);
  }
}

function setMetric(id, value, sub, status) {
  setText(`m-${id}-v`, value);
  setText(`m-${id}-s`, sub || " ");
  const card = $(`m-${id}`);
  if (card && status !== undefined) {
    card.classList.remove("status-good", "status-warning", "status-critical");
    if (status) card.classList.add(`status-${status}`);
  }
}

function systemStatus(d) {
  const h = d.health;
  const dec = d.decision;
  if (h.status === "critical") {
    const bad = Object.values(h.components).find((c) => c.status === "critical");
    return ["Problem", bad ? bad.label : "see pills", "critical"];
  }
  if (d.forecast.safe_mode) return ["Safe mode", (d.forecast.safe_reason || "").startsWith("warming up") ? "warming up" : "forecast not trusted", "warning"];
  if (d.cost.budget_constrained) return ["Budget limit", "capacity capped by budget", "warning"];
  if (dec && dec.current_replicas !== null && dec.ready_replicas !== null && dec.ready_replicas < dec.current_replicas) return ["Scaling", `${dec.ready_replicas}/${dec.current_replicas} pods ready`, "warning"];
  if (d.settings.controller_mode === "observe") return ["Observing", "decisions not applied", "warning"];
  return ["Healthy", "autoscaling active", "good"];
}

function renderProblems(d) {
  const bad = Object.values(d.health.components).filter((c) => c.status === "critical");
  const el = $("problems");
  if (!bad.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="problems" role="alert"><h3>Something needs attention</h3><ul>${bad.map((c) =>
    `<li><b>${esc(c.label)}:</b> ${esc(c.message)}${c.hint ? ` — <span>${esc(c.hint)}</span>` : ""}</li>`).join("")}</ul></div>`;
}

function renderMetrics(d) {
  const t = d.traffic, k = d.kubernetes, f = d.forecast, c = d.cost, dec = d.decision;
  setMetric("users", t.available ? int(t.shoppers) : "—", t.available ? `${t.label || ""} · ${f1(t.target_rps)} req/s offered` : "generator offline");
  setMetric("rps", f1(d.metrics.request_rate), `p95 latency ${isNum(d.metrics.latency_p95_ms) ? Math.round(d.metrics.latency_p95_ms) + " ms" : "—"}`);
  setMetric("pred", f.safe_mode && !isNum(f.peak) ? "—" : f1(f.peak), f.safe_mode ? "safe mode" : `peak in next ${Math.round(f.horizon_seconds)} s`);
  const ready = k.ready_replicas, desired = k.desired_replicas;
  setMetric("pods", isNum(desired) ? String(desired) : "—", isNum(ready) ? `${ready} ready · range ${k.min_replicas}–${k.max_replicas}` : "Kubernetes unavailable");
  setMetric("req", dec && isNum(dec.required_replicas) ? String(dec.required_replicas) : "—", dec && isNum(dec.target_replicas) ? `decision: ${dec.target_replicas} pods` : "");
  setMetric("cost", money(c.current_hourly, c.symbol), `${money(c.per_pod_hour, c.symbol)} per pod-hour`);
  setMetric("budget", money(c.budget, c.symbol), isNum(c.utilization) ? `${Math.round(c.utilization * 100)}% used` : "");
  const [s, sub, st] = systemStatus(d);
  setMetric("status", s, sub, st);
  $("m-cost").classList.toggle("status-warning", isNum(c.utilization) && c.utilization > 0.85);
}

const WHY_ICON = { traffic: "traffic", forecast: "forecast", pods: "pods", cost: "cost", guard: "guard", k8s: "k8s", check: "check", eye: "eye", error: "error", shield: "shield", clock: "clock" };
function renderWhy(d) {
  const w = d.why;
  $("why").innerHTML = `<div class="why-title tone-${esc(w.tone)}">${esc(w.title)}</div>
    <ul class="steps">${w.steps.map((s) => `<li><span class="ic" aria-hidden="true">${ICON[WHY_ICON[s.icon] || "check"]}</span><span>${esc(s.text)}</span></li>`).join("")}</ul>
    ${w.mode_note ? `<div class="mode-note">${esc(w.mode_note)}</div>` : ""}`;
  setText("why-time", d.decision ? `decision #${d.decision.iteration} at ${hhmmss(d.decision.timestamp)}` : "");
}

const POD_ICON = {
  running: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--good)" stroke-width="2" stroke-linejoin="round"><path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z"/><path d="M8.5 12.2l2.4 2.4 4.6-4.8" stroke-linecap="round"/></svg>',
  starting: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--warn)" stroke-width="2" stroke-linejoin="round"><path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z"/><path d="M12 8v4l2.5 1.5" stroke-linecap="round"/></svg>',
  terminating: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--unknown)" stroke-width="2" stroke-linejoin="round"><path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z"/><path d="M9 12h6" stroke-linecap="round"/></svg>',
  failed: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2" stroke-linejoin="round"><path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5" stroke-linecap="round"/></svg>',
};
POD_ICON.pending = POD_ICON.failed;
function renderPods(d) {
  const k = d.kubernetes;
  const pods = k.pods || [];
  let html = pods.map((p) => `<div class="pod ${esc(p.state)}" title="${esc(p.name)}">${POD_ICON[p.state] || POD_ICON.pending}
      <span class="plabel">${esc(p.label)}</span><span class="pname">${esc(p.name.replace(/^marketplace-/, ""))}</span></div>`).join("");
  const active = pods.filter((p) => p.state !== "terminating").length;
  const free = Math.max(0, Math.min(12, k.max_replicas || 8) - active);
  for (let i = 0; i < free; i++) html += '<div class="pod empty"><span>unused slot</span></div>';
  $("pods").innerHTML = html || '<div class="empty-note">No pod information.</div>';
  const running = pods.filter((p) => p.state === "running").length;
  setText("pods-hint", `${running} running`);
  $("pods-foot").textContent = k.pods_error ? `Pod list unavailable: ${k.pods_error.message}` :
    `Deployment ${k.namespace}/${k.deployment} · ${k.backend === "dry-run" ? "DRY RUN (no cluster)" : "live from the Kubernetes API"} · max ${k.max_replicas} pods`;
}

function renderCost(d) {
  const c = d.cost;
  const pct = isNum(c.utilization) ? c.utilization : 0;
  $("cost").innerHTML = `
    <div class="big num">${money(c.current_hourly, c.symbol)}<span class="unit"> / hour now</span></div>
    <div class="bar ${pct > 1 ? "over" : pct > 0.85 ? "warn" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct * 100)}" aria-label="Budget used"><div id="cost-bar"></div></div>
    <div class="kv"><span>Budget</span><span class="num">${money(c.budget, c.symbol)} / hour (${Math.round(pct * 100)}% used)</span></div>
    <div class="kv"><span>Planned (next decision)</span><span class="num">${money(c.planned_hourly, c.symbol)} / hour</span></div>
    <div class="kv"><span>Price per pod</span><span class="num">${money(c.per_pod_hour, c.symbol)} / hour</span></div>
    <div class="kv"><span>Max pods within budget</span><span class="num">${c.max_affordable_replicas ?? "—"}</span></div>
    <div class="kv"><span>Spent since reset</span><span class="num">${money(c.accrued_since_reset, c.symbol, 4)}</span></div>
    ${c.budget_constrained ? '<div class="mode-note">Budget limit reached — CostWise will not exceed the budget.</div>' : ""}
    <div class="fine">${esc(c.label)}</div>`;
  $("cost-bar").style.width = `${Math.min(100, pct * 100)}%`;
}

function renderUsers(d) {
  const t = d.traffic;
  if (!t.available) {
    $("users").innerHTML = `<div class="empty-note">Traffic generator not reachable.${t.error && t.error.hint ? " " + esc(t.error.hint) : ""}</div>`;
    setText("users-hint", "");
    return;
  }
  const perDot = 20;
  const on = Math.min(200, Math.round((t.shoppers || 0) / perDot));
  let dots = "";
  for (let i = 0; i < 200; i++) dots += `<i class="${i < on ? "on" : ""}"></i>`;
  $("users").innerHTML = `<div class="big num">${int(t.shoppers)}</div>
    <div class="crowd" aria-hidden="true">${dots}</div>
    <div class="kv"><span>Scenario</span><span>${esc(t.label || "—")}</span></div>
    <div class="kv"><span>Requests sent</span><span class="num">${f1(t.achieved_rps)} / ${f1(t.target_rps)} req/s</span></div>
    <div class="kv"><span>Shopper success</span><span class="num">${isNum(t.success_ratio) ? (t.success_ratio * 100).toFixed(1) + "%" : "—"}</span></div>
    <div class="fine">Each dot = ${perDot} shoppers. Every shopper sends real HTTP requests (≈1 page every ${isNum(t.rate_per_shopper) && t.rate_per_shopper > 0 ? Math.round(1 / t.rate_per_shopper) : "—"} s).</div>`;
  setText("users-hint", "simulated, real requests");
}

function renderComparison(d) {
  const c = d.comparison;
  if (!c.available) { $("cmp").innerHTML = `<div class="empty-note">${esc(c.message)}</div>`; return; }
  $("cmp").innerHTML = `
    <div class="cmp-row"><span>Scale-ups ahead of the load</span><b class="num">${c.ahead_of_load} / ${c.scale_ups}</b></div>
    <div class="cmp-row"><span>Average head start</span><b class="num">${isNum(c.avg_lead_seconds) ? Math.round(c.avg_lead_seconds) + " s" : "—"}</b></div>
    <div class="cmp-row"><span>Pod start-up time (measured)</span><b class="num">${isNum(c.avg_startup_seconds) ? Math.round(c.avg_startup_seconds) + " s" : "—"}</b></div>
    <p class="cmp-msg">${esc(c.message)}</p>
    <div class="fine">A reactive autoscaler (e.g. HPA) only sees load that already arrived, so it starts pods at the earliest when the load exceeds capacity — then shoppers wait for the start-up time. Full comparison: <code>python -m experiments.run_experiment</code>.</div>`;
}

const EV_SYMBOL = { info: "i", success: "✓", warning: "!", error: "✕" };
function renderTimeline(d) {
  const items = [...d.events].reverse();
  $("timeline").innerHTML = items.length ? items.map((e) => `<li class="ev-${esc(e.level)}"><time>${hhmmss(e.timestamp)}</time>
      <span class="ev-ic" aria-label="${esc(e.level)}">${EV_SYMBOL[e.level] || "i"}</span>
      <span><span class="ev-cat">${esc(e.category)}</span>${esc(e.message)}</span></li>`).join("")
    : '<li class="empty-note">No events yet. Press START DEMO.</li>';
}

function renderGuards(d) {
  const s = d.settings;
  const sym = d.cost.symbol;
  $("guards").innerHTML = `<table><tbody>
    <tr><td>Pod range</td><td class="n">${s.min_replicas} – ${s.max_replicas}</td></tr>
    <tr><td>Budget</td><td class="n">${money(s.max_budget, sym)} / hour</td></tr>
    <tr><td>Target load per pod</td><td class="n">${Math.round(s.target_utilization * 100)}% of ${s.capacity_per_pod} req/s</td></tr>
    <tr><td>Safety margin</td><td class="n">${Math.round(s.safety_margin * 100)}%</td></tr>
    <tr><td>Max step</td><td class="n">+${s.max_scale_up_step} / −${s.max_scale_down_step} pods</td></tr>
    <tr><td>Cooldown up / down</td><td class="n">${s.scale_up_cooldown} s / ${s.scale_down_cooldown} s</td></tr>
    <tr><td>Scale-down stabilisation</td><td class="n">${s.stabilization_window} s</td></tr>
    <tr><td>Min forecast confidence</td><td class="n">${Math.round(s.min_forecast_confidence * 100)}%</td></tr>
    <tr><td>Decision every</td><td class="n">${s.scaling_interval} s</td></tr>
    <tr><td>Mode</td><td class="n">${esc(s.controller_mode)} · ${esc(s.optimizer_mode.replace("_", "-"))}</td></tr>
  </tbody></table><div class="fine">If the forecast is missing or not trusted, CostWise enters safe mode: it plans on measured traffic and never removes pods.</div>`;
}

function renderScenarioNote(d) {
  const t = d.traffic;
  const r = d.model.retrain || {};
  let text = t.available ? `<b>${esc(t.label || "")}</b> — ${esc(t.description || "")}` : "Traffic generator offline.";
  if (r.status === "running") text += ` · <b>Retraining the LSTM…</b>`;
  else if (r.status === "succeeded" || r.status === "failed") text += ` · ${esc(r.message || "")}`;
  $("scenario-note").innerHTML = text;
  const busy = r.status === "running";
  const btn = view.querySelector('[data-action="retrain"]');
  if (btn) btn.disabled = busy;
}

function renderDashboard(d) {
  if (!$("why")) dashboardSkeleton();
  renderProblems(d);
  renderMetrics(d);
  renderWhy(d);
  renderPods(d);
  renderCost(d);
  renderUsers(d);
  renderComparison(d);
  renderTimeline(d);
  renderGuards(d);
  renderScenarioNote(d);
  const empty = !d.series.points.some((p) => isNum(p.actual));
  $("chart-empty").textContent = empty ? (d.health.components.prometheus.status === "good"
    ? "Waiting for the first measurements… press START DEMO." : "No measurements: Prometheus is not available.") : "";
  setText("chart-hint", `last ${Math.round(d.series.points.length * d.series.interval_seconds / 60)} min · 1 point every ${d.series.interval_seconds} s`);
  drawChart($("chart"), d.series, d.time);
}

// ------------------------------------------------------------------ technical page
function metricRow(label, lstm, naive, holt, unit = "req/s") {
  return `<tr><td>${label}</td><td class="n">${f1(lstm, 2)}</td><td class="n">${f1(naive, 2)}</td><td class="n">${f1(holt, 2)}</td><td>${unit}</td></tr>`;
}

async function renderTechnical() {
  view.innerHTML = '<p class="empty-note">Loading technical details…</p>';
  let t;
  try { t = await getJSON("/api/technical"); } catch (err) { view.innerHTML = `<div class="problems">${esc(err.message)}</div>`; return; }
  const m = t.model, last = t.last_decision, cfg = t.config, sym = t.inputs.currency_symbol;
  const chosen = last ? last.desired_replicas : null;
  view.innerHTML = `
  <div class="tech-grid">
    <div class="card"><div class="panel-title"><h2>Forecast model</h2><span class="hint">${esc(m.model || "")}</span></div>
      ${m.available ? `<table><tbody>
        <tr><td>Last trained</td><td class="n">${esc(m.trained_at || "—")} (${f1(m.training_seconds)} s)</td></tr>
        <tr><td>Architecture</td><td class="n">${esc(t.model_runtime.architecture || "—")}</td></tr>
        <tr><td>Parameters</td><td class="n">${int(m.parameters)}</td></tr>
        <tr><td>Input window (sequence length)</td><td class="n">${m.sequence_length} steps = ${m.history_seconds} s</td></tr>
        <tr><td>Forecast horizon</td><td class="n">${m.horizon} steps = ${m.horizon_seconds} s</td></tr>
        <tr><td>Training data</td><td class="n"><span class="tag synthetic">synthetic</span> ${int(m.bootstrap_points)} bootstrap points<br><span class="tag real">real</span> ${int(m.observed_points)} measured points</td></tr>
        <tr><td>Windows train / val / test</td><td class="n">${int(m.train_windows)} / ${int(m.validation_windows)} / ${int(m.test_windows)}</td></tr>
        <tr><td>Measured history available for retraining</td><td class="n">${int(m.observed_rows_available)} samples (${m.observed_minutes_available} min)</td></tr>
        <tr><td>Continuous / online learning</td><td class="n">No — only when you press RETRAIN MODEL</td></tr>
      </tbody></table>
      <p class="fine">${esc(m.bootstrap_label || "")}. ${esc(m.observed_label || "")}.</p>
      <h3>Offline test error (held-out data, all ${m.horizon} horizon steps)</h3>
      <table><thead><tr><th>Metric</th><th class="n">LSTM</th><th class="n">Naive</th><th class="n">Holt</th><th></th></tr></thead><tbody>
        ${metricRow("MAE", m.mae, m.naive_mae, m.holt_mae)}
        ${metricRow("RMSE", m.rmse, m.naive_rmse, m.holt_rmse)}
        <tr><td>MAPE</td><td class="n">${f1(m.mape)}%</td><td></td><td></td><td></td></tr>
      </tbody></table>
      <p class="fine">RMSE improvement over the naive "no change" forecast: ${f1(m.rmse_improvement_vs_naive_pct)}%.</p>
      <h3>Error by horizon step</h3>
      <table><thead><tr><th>Step</th><th class="n">Seconds ahead</th><th class="n">MAE</th><th class="n">RMSE</th></tr></thead><tbody>
        ${m.per_horizon.map((p) => `<tr><td>${p.step}</td><td class="n">${p.step * m.step_seconds}</td><td class="n">${f1(p.mae, 2)}</td><td class="n">${f1(p.rmse, 2)}</td></tr>`).join("")}
      </tbody></table>` : '<p class="problems">No trained model found. Press RETRAIN MODEL or run <code>python -m ml.src.lite train</code>. CostWise runs in safe mode meanwhile.</p>'}
      <h3>Live accuracy (this session)</h3>
      <table><tbody>
        <tr><td>One-step MAE / RMSE</td><td class="n">${f1(last && last.forecast_mae, 2)} / ${f1(last && last.forecast_rmse, 2)} req/s</td></tr>
        <tr><td>Confidence (trust score)</td><td class="n">${last && isNum(last.forecast_confidence) ? Math.round(last.forecast_confidence * 100) + "%" : "not enough samples yet"}</td></tr>
      </tbody></table>
      <p class="fine">Trust score = 1 − LSTM error ÷ (error of "traffic stays the same" + one pod of traffic), on the last 2 minutes of matured 30-second forecasts. Below ${Math.round(t.inputs.min_forecast_confidence * 100)}% CostWise switches to safe mode.</p>
      <p><button class="btn" id="retrain-tech">${ICON.brain}RETRAIN MODEL</button> <span class="fine" id="retrain-state">${esc((m.retrain || {}).message || "")}</span></p>
    </div>

    <div class="card"><div class="panel-title"><h2>Last decision: optimiser candidates</h2><span class="hint">${last ? "#" + last.iteration : ""}</span></div>
      ${last ? `<p class="fine">Objective J(r) = cost(r) + ${t.inputs.sla_penalty_per_rps} × expected shortfall(r) + ${t.inputs.change_penalty} × |r − current|. Effective demand ${f1(last.effective_demand)} req/s, σ = ${f1(last.sigma, 2)}.</p>
      <table><thead><tr><th class="n">Pods</th><th class="n">Cost/h</th><th class="n">Capacity</th><th class="n">Shortfall</th><th class="n">J(r)</th><th>Budget</th></tr></thead><tbody>
        ${(last.optimizer_candidates || []).map((c) => `<tr class="${c.replicas === chosen ? "chosen" : ""}"><td class="n">${c.replicas}</td><td class="n">${money(c.hourly_cost, sym)}</td><td class="n">${f1(c.capacity)}</td><td class="n">${f1(c.expected_shortfall, 2)}</td><td class="n">${f1(c.objective, 3)}</td><td>${c.within_budget ? "✓ within" : "✕ over"}</td></tr>`).join("")}
      </tbody></table>
      <h3>Raw reasoning</h3><pre class="code">${esc(last.reason)}</pre>
      <h3>Forecast (next ${last.forecast.length} steps, req/s)</h3><pre class="code">${esc(last.forecast.map((v) => v.toFixed(2)).join("  "))}</pre>` : '<p class="empty-note">No decision yet.</p>'}
    </div>

    <div class="card"><div class="panel-title"><h2>Settings (live)</h2><span class="hint">validated before use</span></div>
      <form class="settings-form" id="settings-form">
        <label>Budget per hour (${esc(sym)})<input name="max_budget" type="number" step="0.5" min="0.5" value="${cfg.max_budget}"></label>
        <label>Cost per pod-hour (${esc(sym)})<input name="cost_per_replica" type="number" step="0.5" min="0" value="${cfg.cost_per_replica}"></label>
        <label>Min pods<input name="min_replicas" type="number" min="1" max="100" value="${cfg.min_replicas}"></label>
        <label>Max pods<input name="max_replicas" type="number" min="1" max="100" value="${cfg.max_replicas}"></label>
        <label>Target utilisation (0–1)<input name="target_utilization" type="number" step="0.05" min="0.1" max="1" value="${cfg.target_utilization}"></label>
        <label>Safety margin (0–1)<input name="safety_margin" type="number" step="0.05" min="0" max="2" value="${cfg.safety_margin}"></label>
        <label>Optimiser<select name="optimizer_mode"><option value="cost_aware" ${cfg.optimizer_mode === "cost_aware" ? "selected" : ""}>Cost-aware (CostWise)</option><option value="predictive" ${cfg.optimizer_mode === "predictive" ? "selected" : ""}>Predictive only</option></select></label>
        <label>Controller<select name="controller_mode"><option value="active" ${cfg.controller_mode === "active" ? "selected" : ""}>Active (applies decisions)</option><option value="observe" ${cfg.controller_mode === "observe" ? "selected" : ""}>Observe only</option></select></label>
        <div><button class="btn primary" type="submit">Apply settings</button></div>
      </form>
    </div>

    <div class="card"><div class="panel-title"><h2>Security &amp; access</h2></div>
      <table><tbody>
        <tr><td>Kubernetes identity</td><td>${esc(t.security.kubernetes_identity)}</td></tr>
        <tr><td>RBAC permissions</td><td>${t.security.rbac.map(esc).join("<br>")}</td></tr>
        <tr><td>Browser access</td><td>${esc(t.security.browser_access)}</td></tr>
        <tr><td>Traffic-generator token</td><td>${t.security.control_token_configured ? "configured (value never shown)" : "NOT configured"}</td></tr>
      </tbody></table>
      <h3>Recent Kubernetes events (namespace)</h3>
      <table><thead><tr><th>Time</th><th>Reason</th><th>Object</th><th>Message</th></tr></thead><tbody>
        ${(t.kubernetes_events || []).slice(-12).reverse().map((e) => `<tr><td>${e.time ? hhmmss(e.time) : ""}</td><td>${esc(e.reason)}</td><td>${esc(e.object || "")}</td><td>${esc(e.message)}</td></tr>`).join("") || '<tr><td colspan="4">none</td></tr>'}
      </tbody></table>
    </div>

    <div class="card"><div class="panel-title"><h2>Controller</h2></div>
      <table><tbody>
        <tr><td>Iterations</td><td class="n">${int(t.controller.iterations)}</td></tr>
        <tr><td>Scaling actions applied</td><td class="n">${int(t.controller.scale_events)}</td></tr>
        <tr><td>Last error</td><td>${esc(t.controller.last_error || "none")}</td></tr>
        <tr><td>Uptime</td><td class="n">${Math.round(t.controller.uptime_seconds / 60)} min</td></tr>
      </tbody></table>
    </div>

    <div class="card"><div class="panel-title"><h2>Configuration</h2><span class="hint">secrets redacted</span></div>
      <pre class="code">${esc(JSON.stringify(cfg, null, 2))}</pre></div>
  </div>`;
  $("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {};
    for (const [k, v] of fd.entries()) body[k] = ["optimizer_mode", "controller_mode"].includes(k) ? v : Number(v);
    try { await post("/demo/settings", body); toast("Settings applied"); renderTechnical(); }
    catch (err) { toast(`Settings rejected: ${err.message}`, "", true); }
  });
  $("retrain-tech").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await post("/model/retrain"); toast(ACTION_DONE.retrain); } catch (err) { toast(err.message, err.hint, true); }
  });
}

// ------------------------------------------------------------------ architecture page
function box(x, y, w, h, title, sub, hl = false) {
  return `<g class="box ${hl ? "hl" : ""}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12"/>
    <text x="${x + w / 2}" y="${y + (sub ? h / 2 - 4 : h / 2 + 5)}" text-anchor="middle" font-weight="700">${title}</text>
    ${sub ? `<text class="small" x="${x + w / 2}" y="${y + h / 2 + 14}" text-anchor="middle">${sub}</text>` : ""}</g>`;
}
function renderArchitecture() {
  view.innerHTML = `
  <div class="card arch">
    <div class="panel-title"><h2>How CostWise works</h2><span class="hint">one decision every 5 seconds</span></div>
    <svg viewBox="0 0 1200 560" role="img" aria-label="Architecture diagram of CostWise">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>
      <rect class="zone" x="20" y="20" width="620" height="520" rx="16"/><text class="zone-label" x="40" y="46">Minikube cluster · namespace costwise</text>
      <rect class="zone" x="670" y="20" width="510" height="520" rx="16"/><text class="zone-label" x="690" y="46">Your laptop · CostWise control plane (python -m costwise)</text>
      ${box(50, 80, 240, 70, "Traffic generator", "simulated shoppers → real HTTP")}
      ${box(360, 70, 250, 90, "COSTWISE MARKETPLACE", "Deployment · 1–8 pods (Service)", true)}
      ${box(360, 230, 250, 70, "Prometheus", "scrapes every pod every 5 s")}
      ${box(50, 230, 240, 70, "Kubernetes API", "deployments/scale (RBAC)")}
      ${box(50, 400, 240, 80, "Least-privilege RBAC", "ServiceAccount, no cluster-admin")}
      ${box(360, 400, 250, 80, "Optional", "Grafana · HPA baseline (experiments)")}
      ${box(700, 70, 210, 64, "1 · Collector", "Prometheus queries")}
      ${box(950, 70, 210, 64, "2 · LSTM forecast", "next 30 s of traffic", true)}
      ${box(950, 170, 210, 64, "3 · Capacity planner", "req/s → required pods")}
      ${box(700, 170, 210, 64, "4 · Cost optimiser", "cheapest safe pod count", true)}
      ${box(700, 270, 210, 64, "5 · Safety policy", "cooldowns, limits, budget")}
      ${box(950, 270, 210, 64, "6 · Scaler", "patch spec.replicas")}
      ${box(820, 400, 240, 80, "Control-room dashboard", "127.0.0.1:8000 · this page")}
      <path class="flow hot" d="M290 115 L360 115"/>
      <path class="flow" d="M485 160 L485 230"/>
      <path class="flow" d="M610 265 C 660 265 650 102 700 102"/>
      <path class="flow" d="M910 102 L950 102"/>
      <path class="flow" d="M1055 134 L1055 170"/>
      <path class="flow" d="M950 202 L910 202"/>
      <path class="flow" d="M805 234 L805 270"/>
      <path class="flow" d="M910 302 L950 302"/>
      <path class="flow hot" d="M1055 334 C 1055 380 640 360 290 265"/>
      <path class="flow" d="M170 230 L 360 140"/>
      <path class="flow" d="M940 334 L940 400"/>
    </svg>
  </div>
  <div class="tech-grid">
    <div class="card"><div class="panel-title"><h2>The control loop</h2></div>
      <ol class="loop">
        <li><b>Measure</b> — Prometheus counts every request each marketplace pod handles; CostWise reads requests/second, latency and CPU.</li>
        <li><b>Predict</b> — the LSTM looks at the last 60 s of traffic and forecasts the next 30 s.</li>
        <li><b>Plan</b> — one pod handles about 20 req/s; CostWise plans for 70% of that plus a safety margin, giving the <i>required pods</i>.</li>
        <li><b>Optimise cost</b> — it compares every pod count from min to max: price per hour plus a penalty for expected overload, within the budget.</li>
        <li><b>Stay safe</b> — cooldowns, step limits and a scale-down stabilisation window stop flapping; without a trusted forecast it falls back to safe mode.</li>
        <li><b>Act</b> — it patches <code>deployments/scale</code> in the costwise namespace, the only write permission it has.</li>
      </ol></div>
    <div class="card"><div class="panel-title"><h2>Why predictive + cost-aware?</h2></div>
      <p>A reactive autoscaler (like the Kubernetes HPA) only reacts after CPU is already high, and new pods take seconds to start — shoppers feel the delay during a flash sale.</p>
      <p>CostWise starts pods <i>before</i> the rush using the forecast, and removes them when they are no longer needed, choosing the cheapest pod count that still serves the predicted traffic within the budget.</p>
      <p class="fine">All prices are simulated (₹ per pod-hour) to show the trade-off; this is not a cloud bill.</p></div>
  </div>`;
}

// ------------------------------------------------------------------ router / polling
async function poll() {
  if (page !== "dashboard") return;
  clearTimeout(pollTimer);
  try {
    data = await getJSON("/api/dashboard");
    lastOk = Date.now();
    renderPills(data.health);
    renderDashboard(data);
  } catch (err) {
    if (Date.now() - lastOk > 6000) renderPills(null);
  } finally {
    pollTimer = setTimeout(poll, (data && data.refresh_seconds ? data.refresh_seconds * 1000 : POLL_MS));
  }
}

async function pollHealthOnly() {
  try { renderPills(await getJSON("/health")); } catch { renderPills(null); }
}

function route() {
  const hash = location.hash.replace(/^#/, "") || location.pathname;
  page = hash.startsWith("/technical") ? "technical" : hash.startsWith("/architecture") ? "architecture" : "dashboard";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === page));
  clearTimeout(pollTimer);
  view.innerHTML = "";
  if (page === "dashboard") poll();
  else { pollHealthOnly(); page === "technical" ? renderTechnical() : renderArchitecture(); }
  view.focus({ preventScroll: true });
}

// presentation mode + theme
function setPresent(on) {
  document.body.classList.toggle("present", on);
  $("present-btn").setAttribute("aria-pressed", String(on));
  if (on && page !== "dashboard") location.hash = "#/";
  if (data && page === "dashboard") drawChart($("chart"), data.series, data.time);
}
$("present-btn").addEventListener("click", () => setPresent(!document.body.classList.contains("present")));
$("theme-btn").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("costwise-theme", next); } catch { /* storage unavailable */ }
  if (data && page === "dashboard") drawChart($("chart"), data.series, data.time);
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "p" || e.key === "P") setPresent(!document.body.classList.contains("present"));
  if (e.key === "f" || e.key === "F") { if (!document.fullscreenElement) document.documentElement.requestFullscreen?.(); else document.exitFullscreen?.(); }
});
try { const saved = localStorage.getItem("costwise-theme"); if (saved) document.documentElement.dataset.theme = saved; } catch { /* ignore */ }
window.addEventListener("hashchange", route);
window.addEventListener("resize", () => { if (data && page === "dashboard") drawChart($("chart"), data.series, data.time); });
if (location.pathname === "/technical" && !location.hash) location.hash = "#/technical";
if (location.pathname === "/architecture" && !location.hash) location.hash = "#/architecture";
route();
