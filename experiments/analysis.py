"""Metrics used to compare scaling methods (same code for simulated and live runs).

Time-series columns: ``time_s, load_rps, observed_rps, spec_replicas, ready_replicas,
latency_ms, sla_violation, dropped_requests, cost_rate, cumulative_cost, budget,
prediction_next, prediction_peak, desired_replicas, action``.

* **avg/p95/peak latency** - request-weighted mean / 95th percentile / maximum
* **sla_violation_pct** - % of requests offered while latency > SLA or requests were dropped
* **avg/peak replicas**, **scaling_events** (changes of ``spec.replicas``)
* **under_provisioned_seconds** - ready capacity at target utilisation below the offered load
* **scale_up_reaction_time_s** - mean length of under-provisioned episodes (0 = always ahead)
* **estimated_cost** - integral of the estimated hourly cost (all requested pods billed)
* **budget_utilization_avg/peak**, **budget_exceeded_seconds**
* **forecast_mae/rmse** - one-step forecast vs the observed rate one interval later
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def weighted_percentile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    mask = np.isfinite(values) & (weights > 0)
    if not np.any(mask):
        return float("nan")
    v, w = values[mask], weights[mask]
    order = np.argsort(v)
    v, w = v[order], w[order]
    cum = np.cumsum(w) / np.sum(w)
    return float(v[min(int(np.searchsorted(cum, q / 100.0, side="left")), len(v) - 1)])


def _episodes(mask: np.ndarray, dt: float) -> list[float]:
    durations: list[float] = []
    run = 0
    for flag in mask:
        if flag:
            run += 1
        elif run:
            durations.append(run * dt)
            run = 0
    if run:
        durations.append(run * dt)
    return durations


def forecast_errors(ts: pd.DataFrame, interval_s: float) -> tuple[float, float, int]:
    """MAE, RMSE and count of one-step forecasts vs observed rate ``interval_s`` later."""
    if "prediction_next" not in ts or ts["prediction_next"].isna().all():
        return float("nan"), float("nan"), 0
    times = ts["time_s"].to_numpy(dtype=float)
    observed = ts["observed_rps"].to_numpy(dtype=float)
    preds = ts["prediction_next"].to_numpy(dtype=float)
    tick = np.r_[True, np.diff(preds) != 0] & np.isfinite(preds)
    errs = []
    for t, p in zip(times[tick], preds[tick], strict=True):
        if t + interval_s > times[-1]:
            break
        j = int(np.argmin(np.abs(times - (t + interval_s))))
        if np.isfinite(observed[j]):
            errs.append(p - observed[j])
    if not errs:
        return float("nan"), float("nan"), 0
    arr = np.asarray(errs)
    return float(np.mean(np.abs(arr))), float(np.sqrt(np.mean(arr**2))), len(arr)


def summarize(ts: pd.DataFrame, *, scenario: str, method: str, capacity_per_pod: float,
              target_utilization: float, interval_s: float) -> dict[str, Any]:
    """Comparison metrics for one run (non-finite values become ``None``)."""
    if ts.empty:
        raise ValueError("empty time series")
    times = ts["time_s"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 1.0
    load = ts["load_rps"].to_numpy(dtype=float)
    latency = ts["latency_ms"].to_numpy(dtype=float)
    sla = ts["sla_violation"].to_numpy(dtype=float)
    spec = ts["spec_replicas"].to_numpy(dtype=float)
    ready = ts["ready_replicas"].to_numpy(dtype=float)
    cost_rate = ts["cost_rate"].to_numpy(dtype=float)
    budget = float(ts["budget"].iloc[0])
    requests = load * dt
    total = float(np.sum(requests))
    ok = np.isfinite(latency)
    under = load > ready * capacity_per_pod * target_utilization + 1e-9
    episodes = _episodes(under, dt)
    mae, rmse, n_fc = forecast_errors(ts, interval_s)
    summary = {
        "scenario": scenario,
        "method": method,
        "duration_s": float(len(times) * dt),
        "total_requests": total,
        "avg_latency_ms": float(np.sum(latency[ok] * requests[ok]) / max(np.sum(requests[ok]), 1e-9)),
        "p95_latency_ms": weighted_percentile(latency, requests, 95),
        "peak_latency_ms": float(np.nanmax(latency)) if ok.any() else float("nan"),
        "sla_violation_seconds": float(np.sum(sla) * dt),
        "sla_violation_pct": float(100.0 * np.sum(requests * sla) / max(total, 1e-9)),
        "dropped_requests": float(ts.get("dropped_requests", pd.Series([0.0])).fillna(0).sum()),
        "avg_replicas": float(np.mean(spec)),
        "avg_ready_replicas": float(np.mean(ready)),
        "peak_replicas": int(np.max(spec)),
        "scaling_events": int(np.sum(np.diff(spec) != 0)) if len(spec) > 1 else 0,
        "under_provisioned_seconds": float(np.sum(under) * dt),
        "scale_up_reaction_time_s": float(np.mean(episodes)) if episodes else 0.0,
        "estimated_cost": float(ts["cumulative_cost"].iloc[-1]),
        "budget_utilization_avg": float(np.mean(cost_rate) / budget) if budget > 0 else float("nan"),
        "budget_utilization_peak": float(np.max(cost_rate) / budget) if budget > 0 else float("nan"),
        "budget_exceeded_seconds": float(np.sum(cost_rate > budget + 1e-12) * dt),
        "forecast_mae": mae,
        "forecast_rmse": rmse,
        "forecast_samples": n_fc,
    }
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in summary.items()}
