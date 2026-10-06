"""Comparison tables and charts for an experiment run (needs matplotlib).

    python -m experiments.compare_methods                         # latest run
    python -m experiments.compare_methods --run experiments/results/<run-id>

Writes to ``<run>/charts/``: workload vs time, actual vs predicted, replicas vs
time, cost vs time, budget vs cost, HPA vs predictive, HPA vs proposed, forecast
error, plus ``summary_bars.png`` and ``comparison_table.md``. Every number comes
from files produced by ``run_experiment.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

RESULTS_ROOT = Path(__file__).resolve().parent / "results"
METHOD_LABELS = {
    "fixed": "A: Fixed pods (peak)",
    "hpa": "B: Reactive HPA",
    "predictive": "C: Predictive (LSTM)",
    "cost_aware": "D: Predictive + cost-aware",
}
METHOD_COLORS = {"fixed": "#898781", "hpa": "#e34948", "predictive": "#2a78d6", "cost_aware": "#1baf7a"}
TABLE_COLUMNS = [
    ("total_requests", "Total requests", "{:,.0f}"),
    ("avg_latency_ms", "Avg latency (ms)", "{:.1f}"),
    ("p95_latency_ms", "p95 latency (ms)", "{:.0f}"),
    ("peak_latency_ms", "Peak latency (ms)", "{:.0f}"),
    ("sla_violation_pct", "SLA violations (% req)", "{:.2f}"),
    ("avg_replicas", "Avg pods", "{:.2f}"),
    ("peak_replicas", "Peak pods", "{:.0f}"),
    ("scaling_events", "Scaling events", "{:.1f}"),
    ("scale_up_reaction_time_s", "Under-provisioned episode (s)", "{:.1f}"),
    ("estimated_cost", "Est. cost (currency)", "{:.4f}"),
    ("budget_utilization_peak", "Peak budget use", "{:.2f}"),
    ("forecast_mae", "Forecast MAE (req/s)", "{:.2f}"),
    ("forecast_rmse", "Forecast RMSE (req/s)", "{:.2f}"),
]


def latest_run(root: Path = RESULTS_ROOT) -> Path:
    runs = sorted((p for p in root.iterdir() if (p / "summary.csv").is_file()), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise FileNotFoundError(f"no experiment results in {root}; run python -m experiments.run_experiment")
    return runs[-1]


def load_timeseries(run_dir: Path, scenario: str, method: str) -> pd.DataFrame | None:
    files = sorted((run_dir / "timeseries").glob(f"{scenario}__{method}__*.csv"))
    return pd.read_csv(files[0]) if files else None


def _save(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _fmt(value: object, pattern: str) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "-"
    try:
        return pattern.format(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)


def comparison_table(agg: pd.DataFrame) -> str:
    lines = []
    for scenario, group in agg.groupby("scenario", sort=False):
        lines.append(f"\n### Scenario: {scenario}\n")
        lines.append("| Metric | " + " | ".join(METHOD_LABELS.get(m, m) for m in group["method"]) + " |")
        lines.append("|" + "---|" * (len(group) + 1))
        for col, label, pattern in TABLE_COLUMNS:
            if col in group:
                lines.append(f"| {label} | " + " | ".join(_fmt(v, pattern) for v in group[col]) + " |")
    return "\n".join(lines) + "\n"


def _style(ax: plt.Axes, title: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=11)
    ax.set_xlabel("time (s)")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.25)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def plot_scenario(run_dir: Path, scenario: str, methods: list[str], charts: Path, meta: dict[str, float]) -> None:
    loaded = {m: load_timeseries(run_dir, scenario, m) for m in methods}
    series: dict[str, pd.DataFrame] = {m: ts for m, ts in loaded.items() if ts is not None}
    if not series:
        return
    any_ts = next(iter(series.values()))
    cap, util, interval = meta["capacity_per_pod"], meta["target_utilization"], meta["interval_s"]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(any_ts["time_s"], any_ts["load_rps"], label="offered load", lw=1, color="#52514e")
    ax.plot(any_ts["time_s"], any_ts["observed_rps"], label="measured rate (30 s window)", lw=1.8, color="#2a78d6")
    ax.legend()
    _style(ax, f"Workload vs time - {scenario}", "req/s")
    _save(fig, charts / f"workload_vs_time_{scenario}.png")

    preds = [m for m in series if series[m]["prediction_next"].notna().any()]
    if preds:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(any_ts["time_s"], any_ts["observed_rps"], label="actual (measured)", color="#0b0b0b", lw=1.5)
        for m in preds:
            ts = series[m]
            ax.plot(ts["time_s"] + interval, ts["prediction_next"], ls="--", lw=1.2, color=METHOD_COLORS[m],
                    label=f"forecast t+{interval:.0f}s ({METHOD_LABELS[m]})")
        ax.legend(fontsize=8)
        _style(ax, f"Actual vs predicted workload - {scenario}", "req/s")
        _save(fig, charts / f"actual_vs_predicted_{scenario}.png")
        fig, ax = plt.subplots(figsize=(10, 4))
        for m in preds:
            ts = series[m]
            later = np.interp(ts["time_s"] + interval, ts["time_s"], ts["observed_rps"])
            ax.plot(ts["time_s"], ts["prediction_next"] - later, color=METHOD_COLORS[m], label=METHOD_LABELS[m])
        ax.axhline(0, color="black", lw=0.8)
        ax.legend(fontsize=8)
        _style(ax, f"Forecast error (predicted − actual) - {scenario}", "req/s")
        _save(fig, charts / f"forecast_error_{scenario}.png")

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(any_ts["time_s"], np.ceil(any_ts["load_rps"] / (cap * util)), color="#c3c2b7", lw=1,
            label="pods needed by the offered load")
    for m, ts in series.items():
        ax.step(ts["time_s"], ts["spec_replicas"], where="post", label=METHOD_LABELS.get(m, m),
                color=METHOD_COLORS.get(m))
    ax.legend(fontsize=8)
    _style(ax, f"Pods vs time - {scenario}", "pods")
    _save(fig, charts / f"replicas_vs_time_{scenario}.png")

    fig, ax = plt.subplots(figsize=(10, 4))
    for m, ts in series.items():
        ax.plot(ts["time_s"], ts["cumulative_cost"], label=METHOD_LABELS.get(m, m), color=METHOD_COLORS.get(m))
    ax.legend(fontsize=8)
    _style(ax, f"Estimated cumulative cost - {scenario}", "estimated cost")
    _save(fig, charts / f"cost_vs_time_{scenario}.png")

    fig, ax = plt.subplots(figsize=(10, 4))
    for m, ts in series.items():
        ax.step(ts["time_s"], ts["cost_rate"], where="post", label=METHOD_LABELS.get(m, m), color=METHOD_COLORS.get(m))
    ax.axhline(float(any_ts["budget"].iloc[0]), color="black", lw=1.2, label="budget per hour")
    ax.legend(fontsize=8)
    _style(ax, f"Cost rate vs budget - {scenario}", "estimated cost per hour")
    _save(fig, charts / f"budget_vs_cost_{scenario}.png")

    for other, fname, title in (("predictive", f"hpa_vs_predictive_{scenario}.png", "HPA vs predictive"),
                                ("cost_aware", f"hpa_vs_proposed_{scenario}.png", "HPA vs CostWise (proposed)")):
        if "hpa" not in series or other not in series:
            continue
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True)
        ax1.plot(any_ts["time_s"], any_ts["load_rps"], color="black", lw=1, label="offered load")
        for m in ("hpa", other):
            ts = series[m]
            ax1.step(ts["time_s"], ts["ready_replicas"] * cap * util, where="post", color=METHOD_COLORS[m],
                     label=f"ready capacity - {METHOD_LABELS[m]}")
            ax2.plot(ts["time_s"], ts["latency_ms"], color=METHOD_COLORS[m], label=METHOD_LABELS[m])
        ax1.legend(fontsize=8)
        _style(ax1, f"{title} - {scenario}", "req/s")
        ax2.set_yscale("log")
        ax2.legend(fontsize=8)
        _style(ax2, "", "latency (ms)")
        _save(fig, charts / fname)


def plot_summary(agg: pd.DataFrame, charts: Path) -> None:
    scenarios = list(dict.fromkeys(agg["scenario"]))
    methods = list(dict.fromkeys(agg["method"]))
    metrics = [("sla_violation_pct", "SLA violations (% of requests)"), ("estimated_cost", "Estimated cost"),
               ("avg_replicas", "Average pods"), ("scaling_events", "Scaling events")]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    x = np.arange(len(scenarios))
    width = 0.8 / max(len(methods), 1)
    for ax, (col, label) in zip(axes.ravel(), metrics, strict=True):
        for i, m in enumerate(methods):
            vals = []
            for s in scenarios:
                row = agg[(agg.scenario == s) & (agg.method == m)]
                vals.append(float(row[col].iloc[0]) if not row.empty else np.nan)
            ax.bar(x + i * width - 0.4 + width / 2, vals, width * 0.92, label=METHOD_LABELS.get(m, m),
                   color=METHOD_COLORS.get(m))
        ax.set_xticks(x, scenarios)
        ax.set_title(label, loc="left")
        ax.grid(alpha=0.25, axis="y")
    axes[0, 0].legend(fontsize=8)
    _save(fig, charts / "summary_bars.png")


def generate_report(run_dir: Path) -> Path:
    agg = pd.read_csv(run_dir / "aggregate.csv")
    charts = run_dir / "charts"
    charts.mkdir(exist_ok=True)
    meta_file = run_dir / "run_metadata.json"
    cfg = json.loads(meta_file.read_text())["autoscaler_config"] if meta_file.is_file() else {}
    meta = {"capacity_per_pod": float(cfg.get("capacity_per_pod", 20.0)),
            "target_utilization": float(cfg.get("target_utilization", 0.7)),
            "interval_s": float(cfg.get("scaling_interval", 5.0))}
    methods = list(dict.fromkeys(agg["method"]))
    for scenario in dict.fromkeys(agg["scenario"]):
        plot_scenario(run_dir, scenario, methods, charts, meta)
    plot_summary(agg, charts)
    (run_dir / "comparison_table.md").write_text(comparison_table(agg), encoding="utf-8")
    return charts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate comparison tables and charts.")
    parser.add_argument("--run", type=Path, default=None)
    args = parser.parse_args(argv)
    run_dir = args.run or latest_run()
    generate_report(run_dir)
    print((run_dir / "comparison_table.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
