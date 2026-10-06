"""Decision records and thread-safe controller state shared with the dashboard."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DecisionRecord:
    """Everything the controller observed, predicted and decided in one iteration."""

    timestamp: float
    iteration: int
    controller_mode: str
    optimizer_mode: str
    scaler_backend: str
    forecaster: str | None
    current_replicas: int | None = None
    ready_replicas: int | None = None
    observed_load: float | None = None
    latency_p95_ms: float | None = None
    cpu_cores: float | None = None
    memory_bytes: float | None = None
    forecast: list[float] = field(default_factory=list)
    prediction_next: float | None = None
    peak_demand: float | None = None
    forecast_peak: float | None = None
    effective_demand: float | None = None
    sigma: float | None = None
    required_replicas: int | None = None
    desired_replicas: int | None = None
    target_replicas: int | None = None
    action: str = "HOLD"
    applied: bool = False
    reason: str = ""
    blocked_by: str | None = None
    estimated_current_cost: float | None = None
    estimated_future_cost: float | None = None
    max_budget: float | None = None
    budget_utilization: float | None = None
    budget_status: str | None = None
    capacity_status: str | None = None
    cumulative_cost: float = 0.0
    budget_constrained: bool = False
    capacity_shortfall: bool = False
    budget_infeasible: bool = False
    fallback: bool = False
    fallback_reason: str | None = None
    forecast_confidence: float | None = None
    forecast_mae: float | None = None
    forecast_rmse: float | None = None
    forecast_error: float | None = None
    errors: list[str] = field(default_factory=list)
    loop_seconds: float = 0.0
    # --- explainability (dashboard "Why did the system scale?") --------------------------
    inputs: dict[str, Any] = field(default_factory=dict)
    optimizer_candidates: list[dict[str, Any]] = field(default_factory=list)
    max_affordable_replicas: int | None = None
    expected_capacity: float | None = None
    decision_reason: str = ""
    optimizer_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ControllerState:
    """Latest decisions, safe to read from web-server threads."""

    def __init__(self, max_records: int = 720) -> None:
        self._lock = threading.Lock()
        self._records: deque[DecisionRecord] = deque(maxlen=max_records)
        self.started_at = time.time()
        self.iterations = 0
        self.scale_events = 0
        self.consecutive_errors = 0
        self.running = False
        self.last_error: str | None = None
        self.last_iteration_at: float | None = None

    def add(self, record: DecisionRecord) -> None:
        with self._lock:
            self._records.append(record)
            self.iterations = record.iteration
            self.last_iteration_at = time.time()
            if record.applied:
                self.scale_events += 1
            if record.errors:
                self.consecutive_errors += 1
                self.last_error = record.errors[-1]
            else:
                self.consecutive_errors = 0

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self.scale_events = 0
            self.consecutive_errors = 0
            self.last_error = None

    @property
    def last(self) -> DecisionRecord | None:
        with self._lock:
            return self._records[-1] if self._records else None

    def history(self, limit: int = 100, since: float | None = None) -> list[DecisionRecord]:
        with self._lock:
            items = list(self._records)
        if since is not None:
            items = [r for r in items if r.timestamp > since]
        return items[-limit:] if limit > 0 else items

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "started_at": self.started_at,
                "uptime_seconds": time.time() - self.started_at,
                "iterations": self.iterations,
                "scale_events": self.scale_events,
                "consecutive_errors": self.consecutive_errors,
                "last_error": self.last_error,
                "last_iteration_at": self.last_iteration_at,
            }
