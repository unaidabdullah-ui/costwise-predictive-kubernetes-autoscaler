"""Current and historical workload metrics from Prometheus (HTTP API).

Uses ``/api/v1/query`` and ``/api/v1/query_range``. Missing data is never
replaced by zeros: if the workload query returns no series,
:class:`MetricsUnavailableError` is raised and the controller *holds* the
current replica count instead of scaling on bad data.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import requests

logger = logging.getLogger(__name__)


class MetricsUnavailableError(RuntimeError):
    """Prometheus is unreachable or returned no usable data."""

    hint = "Check that Prometheus is running and its port-forward is up (re-run .\\start-demo.ps1)."


@dataclass(frozen=True)
class WorkloadSnapshot:
    timestamp: float
    request_rate: float
    history: np.ndarray = field(repr=False)
    latency_p95_ms: float | None = None
    cpu_cores: float | None = None
    memory_bytes: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "request_rate": self.request_rate,
            "history_points": int(len(self.history)),
            "latency_p95_ms": self.latency_p95_ms,
            "cpu_cores": self.cpu_cores,
            "memory_bytes": self.memory_bytes,
        }


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


DEFAULT_TARGETS_UP_QUERY = 'count(up{job="marketplace"} == 1)'


class PrometheusMetricsSource:
    """Small, testable client for the Prometheus HTTP API.

    An empty workload result is ambiguous: either nobody sent a request yet, or
    the store is not being scraped. ``targets_up_query`` resolves it - if at
    least one marketplace pod is scraped successfully the measured load is 0 req/s;
    otherwise :class:`MetricsUnavailableError` is raised.
    """

    def __init__(self, base_url: str, workload_query: str, latency_query: str | None = None,
                 cpu_query: str | None = None, memory_query: str | None = None, timeout_seconds: float = 4.0,
                 bearer_token: str = "", step_seconds: float = 5.0, session: requests.Session | None = None,
                 clock: Callable[[], float] = time.time,
                 targets_up_query: str | None = DEFAULT_TARGETS_UP_QUERY) -> None:
        self.targets_up_query = targets_up_query
        self.base_url = base_url.rstrip("/")
        self.workload_query = workload_query
        self.latency_query = latency_query
        self.cpu_query = cpu_query
        self.memory_query = memory_query
        self.timeout = timeout_seconds
        self.step_seconds = step_seconds
        self.session = session or requests.Session()
        self.clock = clock
        if bearer_token:
            self.session.headers["Authorization"] = f"Bearer {bearer_token}"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.ConnectionError as exc:
            raise MetricsUnavailableError(f"Prometheus is not reachable at {self.base_url}") from exc
        except requests.Timeout as exc:
            raise MetricsUnavailableError("Prometheus did not answer in time") from exc
        except (requests.RequestException, ValueError) as exc:
            raise MetricsUnavailableError(f"Prometheus request failed: {exc}") from exc
        if payload.get("status") != "success":
            raise MetricsUnavailableError(f"Prometheus error: {payload.get('error', payload)}")
        return payload["data"]

    def healthy(self) -> bool:
        try:
            return self.session.get(f"{self.base_url}/-/ready", timeout=self.timeout).ok
        except requests.RequestException:
            return False

    def query_vector(self, promql: str) -> list[tuple[dict[str, str], float]]:
        """Instant query returning ``[(labels, value), ...]`` (non-finite values dropped)."""
        data = self._get("/api/v1/query", {"query": promql})
        out = []
        for item in data.get("result", []) if data.get("resultType") == "vector" else []:
            value = _to_float(item["value"][1])
            if value is not None:
                out.append((item.get("metric", {}), value))
        return out

    def query_instant(self, promql: str) -> float | None:
        """Scalar-like instant query (sum over series); ``None`` when there is no data."""
        data = self._get("/api/v1/query", {"query": promql})
        result = data.get("result", [])
        if data.get("resultType") == "scalar":
            return _to_float(result[1]) if result else None
        finite = [v for v in (_to_float(item["value"][1]) for item in result) if v is not None]
        return float(sum(finite)) if finite else None

    def query_range(self, promql: str, start: float, end: float, step: float) -> list[tuple[float, float]]:
        """Range query; ``[(timestamp, value)]`` summed over series."""
        data = self._get("/api/v1/query_range", {"query": promql, "start": start, "end": end, "step": f"{step:g}s"})
        totals: dict[float, float] = {}
        for series in data.get("result", []):
            for ts, raw in series.get("values", []):
                value = _to_float(raw)
                if value is not None:
                    totals[float(ts)] = totals.get(float(ts), 0.0) + value
        return sorted(totals.items())

    def history(self, points: int) -> np.ndarray:
        """Last ``points`` workload samples ``step_seconds`` apart (forward-filled onto a grid)."""
        end = self.clock()
        start = end - (points - 1) * self.step_seconds
        samples = self.query_range(self.workload_query, start, end, self.step_seconds)
        if not samples:
            return np.empty(0)
        grid = start + np.arange(points) * self.step_seconds
        ts = np.array([s[0] for s in samples])
        vals = np.array([s[1] for s in samples])
        idx = np.searchsorted(ts, grid + 1e-6, side="right") - 1
        return vals[idx[idx >= 0]]

    def snapshot(self, history_points: int) -> WorkloadSnapshot:
        """Current workload plus history; raises if the workload metric is missing."""
        current = self.query_instant(self.workload_query)
        if current is None:
            up = self.query_instant(self.targets_up_query) if self.targets_up_query else None
            if not up:
                raise MetricsUnavailableError(
                    "no marketplace measurements (Prometheus is not scraping any store pod yet)")
            current = 0.0  # store pods are up and scraped, but no request has been counted
        hist = self.history(history_points)
        if hist.size == 0:
            hist = np.array([current])

        def optional(query: str | None) -> float | None:
            if not query:
                return None
            try:
                return self.query_instant(query)
            except MetricsUnavailableError:
                return None

        return WorkloadSnapshot(self.clock(), max(current, 0.0), np.maximum(hist, 0.0),
                                optional(self.latency_query), optional(self.cpu_query),
                                optional(self.memory_query))
