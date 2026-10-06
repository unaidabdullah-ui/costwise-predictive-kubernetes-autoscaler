"""Live experiment harness on Minikube (``run_experiment.py --mode live``).

Runs through the CostWise dashboard that ``start-demo`` started (no extra
credentials needed) plus ``kubectl`` for the HPA baseline. For every
(scenario, method):

1. configure the method
   * ``fixed``      - CostWise in *observe* mode, marketplace fixed at the peak pod count
   * ``hpa``        - CostWise in *observe* mode + ``kubernetes/experiments/hpa-baseline.yaml``
                      (needs ``minikube addons enable metrics-server``)
   * ``predictive`` - HPA removed, CostWise active with ``optimizer_mode=predictive``
   * ``cost_aware`` - HPA removed, CostWise active with ``optimizer_mode=cost_aware``
2. reset the demo (marketplace back to the minimum, traffic stopped) and settle;
3. replay the scenario's shopper plans through the traffic generator;
4. sample the dashboard snapshot (Prometheus + Kubernetes + controller) every 5 s;
5. summarise with :func:`experiments.analysis.summarize` (``kind = live``).

Only the plan-based scenarios (``flash_sale``, ``busy``, ``burst``) can be replayed
live because the traffic generator speaks in shopper plans.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from autoscaler.config import AutoscalerConfig
from experiments.analysis import summarize

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HPA_MANIFEST = PROJECT_ROOT / "kubernetes" / "experiments" / "hpa-baseline.yaml"
LIVE_PLANS: dict[str, list[tuple[str, float]]] = {
    "flash_sale": [("normal", 120.0), ("flash_sale", 300.0), ("stopped", 180.0)],
    "busy": [("normal", 90.0), ("busy", 330.0), ("recovery", 180.0)],
    "burst": [("normal", 60.0), ("burst", 300.0), ("normal", 60.0)],
}
SAMPLE_SECONDS = 5.0
HEADERS = {"X-CostWise": "1", "Content-Type": "application/json"}


def kubectl(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["kubectl", *args], capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def post(url: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.post(f"{url}{path}", json=body or {}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def configure(method: str, url: str, namespace: str, peak_pods: int) -> None:
    if method in ("fixed", "hpa"):
        post(url, "/demo/settings", {"controller_mode": "observe"})
        if method == "hpa":
            kubectl("apply", "-f", str(HPA_MANIFEST))
        else:
            kubectl("delete", "-f", str(HPA_MANIFEST), "--ignore-not-found")
            kubectl("-n", namespace, "scale", "deployment/marketplace", f"--replicas={peak_pods}")
    else:
        kubectl("delete", "-f", str(HPA_MANIFEST), "--ignore-not-found")
        post(url, "/demo/settings", {"controller_mode": "active", "optimizer_mode": method})


def sample(url: str, t: float, offered: float, cfg: AutoscalerConfig) -> dict[str, Any]:
    snap = requests.get(f"{url}/api/dashboard", timeout=10).json()
    k = snap.get("kubernetes", {})
    d = snap.get("decision") or {}
    spec = k.get("desired_replicas") or d.get("current_replicas") or 0
    ready = k.get("ready_replicas") or 0
    p95 = (snap.get("metrics") or {}).get("latency_p95_ms")
    err = (snap.get("metrics") or {}).get("error_ratio") or 0.0
    cost_rate = spec * cfg.cost_per_replica
    return {
        "time_s": t, "load_rps": offered, "observed_rps": (snap.get("metrics") or {}).get("request_rate"),
        "spec_replicas": spec, "ready_replicas": ready, "latency_ms": p95,
        "sla_violation": int((p95 is not None and p95 > 250) or err > 0.01),
        "dropped_requests": 0.0, "cost_rate": cost_rate, "budget": cfg.max_budget,
        "prediction_next": d.get("prediction_next"), "prediction_peak": d.get("forecast_peak"),
        "desired_replicas": d.get("desired_replicas"), "action": d.get("action"),
    }


def run_live(args: argparse.Namespace, cfg: AutoscalerConfig, out_dir: Path) -> pd.DataFrame:
    url = args.dashboard_url.rstrip("/")
    try:
        requests.get(f"{url}/health", timeout=5).raise_for_status()
    except requests.RequestException:
        sys.exit(f"ERROR: the CostWise dashboard is not reachable at {url}. Run .\\start-demo.ps1 first.")
    namespace = cfg.kubernetes_namespace
    ts_dir = out_dir / "timeseries"
    ts_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    try:
        for scen in args.scenarios:
            if scen not in LIVE_PLANS:
                print(f"skipping {scen}: live mode supports {sorted(LIVE_PLANS)}")
                continue
            plans = LIVE_PLANS[scen]
            from simulator.scenarios import get_scenario

            offered = get_scenario(scen).profile(1.0, seed=42)
            peak_pods = min(cfg.max_replicas, int(np.ceil(offered.max() * (1 + cfg.safety_margin)
                                                       / cfg.replica_capacity_at_target)))
            for method in args.methods:
                print(f"\n=== live: scenario={scen} method={method} ===", flush=True)
                post(url, "/demo/reset")
                configure(method, url, namespace, peak_pods)
                time.sleep(args.settle)
                post(url, "/demo/start")
                series: list[dict[str, Any]] = []
                t0 = time.time()
                boundaries = np.cumsum([s for _, s in plans])
                current = None
                while (t := time.time() - t0) < boundaries[-1] + 30:
                    idx = int(np.searchsorted(boundaries, t, side="right"))
                    if idx < len(plans) and plans[idx][0] != current:
                        current = plans[idx][0]
                        post(url, "/demo/traffic", {"scenario": current})
                    series.append(sample(url, t, float(offered[min(int(t), len(offered) - 1)]), cfg))
                    time.sleep(SAMPLE_SECONDS)
                ts = pd.DataFrame(series)
                ts["cumulative_cost"] = (ts["cost_rate"] * ts["time_s"].diff().fillna(0) / 3600).cumsum()
                ts.to_csv(ts_dir / f"{scen}__{method}__live.csv", index=False)
                summary = summarize(ts, scenario=scen, method=method, capacity_per_pod=cfg.capacity_per_pod,
                                    target_utilization=cfg.target_utilization, interval_s=cfg.scaling_interval)
                summary.update(seed=42, forecaster=None, fixed_replicas=peak_pods if method == "fixed" else None)
                rows.append(summary)
    finally:
        print("\nRestoring CostWise (active, cost-aware) and removing the HPA ...")
        kubectl("delete", "-f", str(HPA_MANIFEST), "--ignore-not-found", check=False)
        try:
            post(url, "/demo/reset")
        except requests.RequestException:
            pass
    return pd.DataFrame(rows)
