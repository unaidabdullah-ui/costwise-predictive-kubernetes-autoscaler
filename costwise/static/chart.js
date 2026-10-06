// Dependency-free SVG chart: measured traffic, the LSTM forecast and ready capacity, with a NOW marker.

const NS = "http://www.w3.org/2000/svg";
const WINDOW_SECONDS = 360; // six minutes of history on screen

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function el(name, attrs, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (parent) parent.appendChild(node);
  return node;
}

function niceMax(v) {
  if (!(v > 0)) return 10;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10]) if (m * exp >= v) return m * exp;
  return 10 * exp;
}

function pathFrom(points, x, y, step = false) {
  let d = "";
  let pen = false;
  let prev = null;
  for (const p of points) {
    if (p.v === null || p.v === undefined || !Number.isFinite(p.v)) { pen = false; prev = null; continue; }
    const px = x(p.t).toFixed(1), py = y(p.v).toFixed(1);
    if (!pen) { d += `M${px} ${py}`; pen = true; }
    else if (step && prev) d += `H${px}V${py}`;
    else d += `L${px} ${py}`;
    prev = p;
  }
  return d;
}

export function drawChart(svg, series, now) {
  if (!svg) return;
  const W = Math.max(320, svg.clientWidth || 800);
  const H = Math.max(200, svg.clientHeight || 360);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const m = { l: 48, r: 18, t: 18, b: 30 };
  const horizon = series.horizon_seconds || 30;
  const t1 = now + horizon + 4;
  const t0 = t1 - WINDOW_SECONDS - horizon;
  const pts = series.points.filter((p) => p.t >= t0 - 10);
  const actual = pts.map((p) => ({ t: p.t, v: p.actual }));
  const capacity = pts.map((p) => ({ t: p.t, v: p.capacity }));
  if (capacity.length) capacity.push({ t: now, v: capacity[capacity.length - 1].v });
  const fcPast = series.forecast_horizon.filter((p) => p.t >= t0 && p.t <= t1);
  const future = series.future || [];
  const values = [...actual, ...capacity, ...fcPast, ...future].map((p) => p.v).filter((v) => Number.isFinite(v));
  const yMax = niceMax(Math.max(10, ...values) * 1.12);
  const x = (t) => m.l + ((t - t0) / (t1 - t0)) * (W - m.l - m.r);
  const y = (v) => H - m.b - (v / yMax) * (H - m.t - m.b);

  // grid + axes
  const grid = el("g", { class: "axis" }, svg);
  for (let i = 0; i <= 4; i++) {
    const v = (yMax / 4) * i;
    el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: "gridline" }, grid);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, grid).textContent = Number.isInteger(v) ? v : v.toFixed(1);
  }
  el("text", { x: 12, y: m.t + (H - m.t - m.b) / 2, transform: `rotate(-90 12 ${m.t + (H - m.t - m.b) / 2})`, "text-anchor": "middle" }, grid).textContent = "requests / second";
  for (let s = -300; s <= 0; s += 60) {
    const tx = x(now + s);
    if (tx < m.l) continue;
    el("text", { x: tx, y: H - 8, "text-anchor": "middle" }, grid).textContent = s === 0 ? "now" : `${s / 60} min`;
  }
  el("text", { x: x(now + horizon), y: H - 8, "text-anchor": "middle" }, grid).textContent = `+${Math.round(horizon)} s`;

  // future band
  const band = el("rect", { x: x(now), y: m.t, width: Math.max(0, x(now + horizon) - x(now)), height: H - m.t - m.b, fill: css("--forecast"), opacity: 0.08 }, svg);
  band.appendChild(document.createElementNS(NS, "title")).textContent = `Forecast window: the next ${Math.round(horizon)} seconds`;

  // capacity (step)
  el("path", { d: pathFrom(capacity, x, y, true), fill: "none", stroke: css("--capacity"), "stroke-width": 2, opacity: 0.85 }, svg);
  // past forecasts aligned to the time they were made for
  el("path", { d: pathFrom(fcPast, x, y), fill: "none", stroke: css("--forecast"), "stroke-width": 2.2, "stroke-dasharray": "7 5" }, svg);
  // actual
  el("path", { d: pathFrom(actual, x, y), fill: "none", stroke: css("--actual"), "stroke-width": 3.2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
  // future forecast path
  if (future.length > 1) {
    el("path", { d: pathFrom(future, x, y), fill: "none", stroke: css("--forecast"), "stroke-width": 3, "stroke-dasharray": "2 6", "stroke-linecap": "round" }, svg);
    const peak = future.slice(1).reduce((a, b) => (b.v > a.v ? b : a), future[1]);
    el("circle", { cx: x(peak.t), cy: y(peak.v), r: 5, fill: css("--forecast") }, svg);
    const label = el("text", { x: Math.min(W - m.r - 4, x(peak.t)), y: Math.max(m.t + 12, y(peak.v) - 10), "text-anchor": "end", fill: css("--forecast"), "font-size": 12, "font-weight": 700 }, svg);
    label.textContent = `forecast ${peak.v.toFixed(1)}`;
  }
  // latest actual dot
  const lastActual = [...actual].reverse().find((p) => Number.isFinite(p.v));
  if (lastActual) el("circle", { cx: x(lastActual.t), cy: y(lastActual.v), r: 5, fill: css("--actual") }, svg);
  // NOW marker
  el("line", { x1: x(now), x2: x(now), y1: m.t - 6, y2: H - m.b, stroke: css("--ink"), "stroke-width": 1.5, "stroke-dasharray": "3 3" }, svg);
  el("text", { x: x(now) - 6, y: m.t + 4, "text-anchor": "end", fill: css("--ink"), "font-size": 12, "font-weight": 800 }, svg).textContent = "NOW";

  // accessible summary
  const fcPeak = future.length > 1 ? Math.max(...future.slice(1).map((p) => p.v)) : null;
  svg.setAttribute("aria-label", `Traffic chart. Current load ${lastActual ? lastActual.v.toFixed(1) : "unknown"} requests per second` +
    (fcPeak !== null ? `; LSTM forecast peak ${fcPeak.toFixed(1)} in the next ${Math.round(horizon)} seconds.` : "."));
}
