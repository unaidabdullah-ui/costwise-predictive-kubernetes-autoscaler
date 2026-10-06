"""Run the scaling-method comparison.

Methods (all on identical traffic):
  A ``fixed``       fixed number of pods provisioned for the scenario's peak
  B ``hpa``         reactive Kubernetes HPA (emulated in ``sim``, the real HPA in ``live``)
  C ``predictive``  LSTM forecast, pods = required pods (no cost optimisation)
  D ``cost_aware``  LSTM forecast + cost-aware optimisation under the budget (CostWise)

Modes:
  ``sim``  (default) discrete-time simulation driven by the production controller
           code - deterministic, seconds per run, no cluster needed::

               python -m experiments.run_experiment
               python -m experiments.run_experiment --seeds 1 2 3 --budget 25

  ``live`` real Minikube run through the running dashboard (start-demo first)::

               python -m experiments.run_experiment --mode live --scenarios flash_sale

Results go to ``experiments/results/<run-id>/`` (time series, summary.csv/json,
aggregate.csv, run_metadata.json, comparison_table.md, charts/). Nothing is
pre-computed: every number comes from running this command.
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from autoscaler.config import AutoscalerConfig
from common.env import load_dotenv
from common.jsonutil import write_json
from common.logging_utils import configure_logging
from experiments.analysis import summarize
from ml.src.forecasters import FORECASTER_NAMES, Forecaster, ModelNotAvailableError, load_forecaster
from simulator.cluster_simulator import METHODS, SimulationConfig, run_simulation
from simulator.scenarios import EXPERIMENT_SCENARIOS, SCENARIOS, get_scenario

logger = logging.getLogger(__name__)
RESULTS_ROOT = Path(__file__).resolve().parent / "results"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare fixed, reactive HPA, predictive and cost-aware scaling.")
    parser.add_argument("--mode", choices=("sim", "live"), default="sim")
    parser.add_argument("--scenarios", nargs="+", default=list(EXPERIMENT_SCENARIOS), choices=sorted(SCENARIOS))
    parser.add_argument("--methods", nargs="+", default=list(METHODS), choices=METHODS)
    parser.add_argument("--forecaster", default="lstm_lite", choices=FORECASTER_NAMES)
    parser.add_argument("--model", type=Path, default=None, help="model file (default: the shipped LSTM)")
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--budget", type=float, default=None, help="override BUDGET_PER_HOUR")
    parser.add_argument("--pod-startup", type=float, default=12.0, help="pod readiness delay in seconds (sim)")
    parser.add_argument("--warmup", type=float, default=120.0, help="steady-state warm-up seconds (sim)")
    parser.add_argument("--sla-ms", type=float, default=250.0)
    parser.add_argument("--scale", type=float, default=1.0, help="multiply scenario rates")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8000", help="live mode: CostWise dashboard")
    parser.add_argument("--settle", type=float, default=60.0, help="live mode: seconds between runs")
    return parser


def make_forecaster_factory(args: argparse.Namespace, cfg: AutoscalerConfig) -> tuple[Any, dict[str, Any]]:
    try:
        forecaster: Forecaster = load_forecaster(args.forecaster, args.model, cfg.prediction_horizon,
                                                 cfg.sequence_length)
    except ModelNotAvailableError as exc:
        sys.exit(f"ERROR: {exc}\nTrain it (python -m ml.src.lite train) or use --forecaster holt.")
    return (lambda: forecaster), forecaster.info()


def run_sim(args: argparse.Namespace, cfg: AutoscalerConfig, out_dir: Path) -> pd.DataFrame:
    needs = any(m in ("predictive", "cost_aware") for m in args.methods)
    factory, fc_info = make_forecaster_factory(args, cfg) if needs else (None, {})
    if needs:  # the controller must run at the model's step and horizon
        cfg = cfg.with_overrides(
            prediction_horizon=int(fc_info.get("horizon") or cfg.prediction_horizon),
            sequence_length=int(fc_info.get("sequence_length") or cfg.sequence_length),
            scaling_interval=float(fc_info.get("step_seconds") or cfg.scaling_interval),
        )
    ts_dir = out_dir / "timeseries"
    ts_dir.mkdir(parents=True, exist_ok=True)
    logging.getLogger("autoscaler").setLevel(logging.ERROR)
    rows: list[dict[str, Any]] = []
    for seed in args.seeds:
        sim_cfg = SimulationConfig(seed=seed, pod_startup_seconds=args.pod_startup, sla_latency_ms=args.sla_ms,
                                   scale=args.scale, warmup_seconds=args.warmup)
        for scen_name in args.scenarios:
            scenario = get_scenario(scen_name)
            for method in args.methods:
                started = time.perf_counter()
                result = run_simulation(scenario, method, cfg, sim_cfg,
                                        factory if method in ("predictive", "cost_aware") else None)
                result.timeseries.to_csv(ts_dir / f"{scen_name}__{method}__seed{seed}.csv", index=False)
                summary = summarize(result.timeseries, scenario=scen_name, method=method,
                                    capacity_per_pod=cfg.capacity_per_pod,
                                    target_utilization=cfg.target_utilization, interval_s=cfg.scaling_interval)
                summary.update(seed=seed, forecaster=fc_info.get("label") if method in ("predictive", "cost_aware")
                               else None, fixed_replicas=result.config.get("fixed_replicas"))
                rows.append(summary)
                print(f"  {scen_name:<11} {method:<11} seed {seed}: SLA violations {summary['sla_violation_pct']:5.2f} %"
                      f"  avg pods {summary['avg_replicas']:4.2f}  est. cost {summary['estimated_cost']:.3f}"
                      f"  ({time.perf_counter() - started:.1f} s)", flush=True)
    write_json(out_dir / "forecaster.json", fc_info)
    return pd.DataFrame(rows)


def aggregate(summary: pd.DataFrame) -> pd.DataFrame:
    numeric = summary.select_dtypes("number").columns.drop(["seed", "fixed_replicas"], errors="ignore")
    agg = summary.groupby(["scenario", "method"], sort=False)[list(numeric)].mean().reset_index()
    agg["runs"] = summary.groupby(["scenario", "method"], sort=False).size().to_numpy()
    return agg


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    configure_logging("WARNING", "text")
    cfg = AutoscalerConfig.from_env()
    if args.budget is not None:
        cfg = cfg.with_overrides(max_budget=args.budget)
    run_id = args.run_id or f"{args.mode}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir = RESULTS_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    print(f"Experiment '{run_id}' ({args.mode}): {len(args.scenarios)} scenario(s) × {len(args.methods)} method(s)")
    if args.mode == "sim":
        summary = run_sim(args, cfg, out_dir)
    else:
        from experiments.live import run_live

        summary = run_live(args, cfg, out_dir)
    summary.to_csv(out_dir / "summary.csv", index=False)
    write_json(out_dir / "summary.json", summary.to_dict(orient="records"))
    agg = aggregate(summary)
    agg.to_csv(out_dir / "aggregate.csv", index=False)
    write_json(out_dir / "run_metadata.json", {
        "run_id": run_id, "mode": args.mode,
        "kind": "simulation (production controller code, simulated cluster)" if args.mode == "sim"
        else "live measurement on Minikube",
        "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "arguments": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "autoscaler_config": cfg.public_dict(), "python": platform.python_version(),
        "platform": platform.platform(),
    })
    if not args.no_plots:
        try:
            from experiments.compare_methods import generate_report

            generate_report(out_dir)
        except ImportError as exc:
            print(f"(charts skipped: {exc}; install requirements/full.txt)")
    cols = ["scenario", "method", "sla_violation_pct", "p95_latency_ms", "avg_replicas", "scaling_events",
            "estimated_cost", "budget_utilization_peak"]
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(agg[cols].round(3).to_string(index=False))
    print(f"\nResults written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
