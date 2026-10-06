"""Generate grafana/dashboards/costwise.json (run: python grafana/generate_dashboard.py).

Grafana is OPTIONAL (start-demo -WithGrafana). The CostWise dashboard at
127.0.0.1:8000 is the main view; this one shows the same Prometheus data for
people who prefer Grafana. Every panel is a PromQL query on real metrics.
"""

from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
PANELS = [
    ("Requests per second (shop API)", 'sum(rate(marketplace_http_requests_total{route=~"/api/.*"}[30s]))', "reqps"),
    ("p95 latency (ms)", "1000 * histogram_quantile(0.95, sum by (le) "
     '(rate(marketplace_http_request_duration_seconds_bucket{route=~"/api/.*"}[1m])))', "ms"),
    ("Marketplace pods scraped", 'count(up{job="marketplace"} == 1)', "short"),
    ("Error ratio (5xx incl. 503 load shedding)", 'costwise:marketplace_error_ratio:1m', "percentunit"),
    ("CPU per pod (cores)", 'sum by (pod) (rate(process_cpu_seconds_total{job="marketplace"}[1m]))', "short"),
    ("Requests per second by pod", 'sum by (pod) (rate(marketplace_http_requests_total{route=~"/api/.*"}[30s]))',
     "reqps"),
    ("Simulated shoppers", "sum(generator_active_shoppers)", "short"),
    ("Shopper outcomes per second", "sum by (outcome) (rate(generator_requests_total[30s]))", "reqps"),
]


def build() -> dict:
    panels = []
    for i, (title, expr, unit) in enumerate(PANELS):
        panels.append({
            "id": i + 1, "type": "timeseries", "title": title, "datasource": DS,
            "gridPos": {"h": 8, "w": 12, "x": (i % 2) * 12, "y": (i // 2) * 8},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "targets": [{"refId": "A", "datasource": DS, "expr": expr, "legendFormat": "{{pod}}{{outcome}}"}],
        })
    return {"uid": "costwise", "title": "CostWise - Marketplace (Prometheus)", "schemaVersion": 39,
            "time": {"from": "now-15m", "to": "now"}, "refresh": "5s", "tags": ["costwise"], "panels": panels}


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "dashboards" / "costwise.json"
    out.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
