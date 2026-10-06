"""Export historical workload from Prometheus to CSV (for retraining / analysis).

    python -m autoscaler.collector --hours 1 --output .costwise/prometheus-export.csv

Columns: ``timestamp, request_rate`` (+ latency/cpu/memory/pods when available),
the same schema the control loop appends to ``.costwise/history.csv`` - so the
file can be passed to ``python -m ml.src.lite train --history <file>``.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

from autoscaler.config import AutoscalerConfig
from autoscaler.metrics_source import MetricsUnavailableError, PrometheusMetricsSource
from common.env import load_dotenv

PODS_QUERY = 'count(up{job="marketplace"} == 1)'


def collect(source: PrometheusMetricsSource, cfg: AutoscalerConfig, start: float, end: float, step: float
            ) -> list[dict[str, object]]:
    series: dict[str, dict[float, float]] = {}
    for name, query in (("request_rate", cfg.workload_query), ("latency_ms", cfg.latency_query),
                        ("cpu_cores", cfg.cpu_query), ("memory_bytes", cfg.memory_query), ("replicas", PODS_QUERY)):
        try:
            series[name] = dict(source.query_range(query, start, end, step))
        except MetricsUnavailableError:
            series[name] = {}
    if not series["request_rate"]:
        raise MetricsUnavailableError("no workload data in the requested window")
    rows = []
    for ts in sorted(series["request_rate"]):
        row: dict[str, object] = {"timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for name in ("request_rate", "latency_ms", "cpu_cores", "memory_bytes", "replicas"):
            row[name] = series[name].get(ts, "")
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Export workload history from Prometheus to CSV.")
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--step", type=float, default=None, help="sample spacing (default SCALING_INTERVAL)")
    parser.add_argument("--output", type=Path, default=Path(".costwise/prometheus-export.csv"))
    args = parser.parse_args(argv)
    cfg = AutoscalerConfig.from_env()
    step = args.step or cfg.scaling_interval
    source = PrometheusMetricsSource(cfg.prometheus_url, cfg.workload_query, timeout_seconds=10,
                                     bearer_token=cfg.prometheus_bearer_token, step_seconds=step)
    end = time.time()
    rows = collect(source, cfg, end - args.hours * 3600, end, step)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
