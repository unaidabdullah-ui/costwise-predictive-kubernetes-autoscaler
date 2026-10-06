"""Activity timeline: plain-English events derived from real controller decisions.

Every control-loop iteration produces a :class:`~autoscaler.state.DecisionRecord`.
:func:`derive_events` compares it with the previous record and emits an event only
when something *changed* - traffic moved, a new forecast peak, a new pod
requirement, a budget check result, a scaling action, pods became ready, an error
appeared or cleared. The dashboard runtime adds its own events (shoppers changed,
demo buttons) through the same :class:`EventLog`, so the timeline is one story
built only from what actually happened.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from autoscaler.state import DecisionRecord

LEVELS = ("info", "success", "warning", "error")
CATEGORIES = ("traffic", "forecast", "capacity", "cost", "decision", "kubernetes", "system", "demo", "model")

_ERROR_TEXT = {
    "metrics": "Prometheus metrics unavailable - holding the current pods",
    "prediction": "Forecast unavailable",
    "kubernetes": "Kubernetes API error - holding the current pods",
    "optimizer": "Cost optimiser error - holding the current pods",
    "other": "Unexpected controller error",
}


@dataclass(frozen=True)
class Event:
    id: int
    timestamp: float
    level: str
    category: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventLog:
    """Thread-safe bounded ring buffer with incremental reads (``since(id)``)."""

    def __init__(self, max_events: int = 300, clock: Callable[[], float] = time.time) -> None:
        self._events: deque[Event] = deque(maxlen=max_events)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._clock = clock

    def emit(self, message: str, *, level: str = "info", category: str = "system",
             data: dict[str, Any] | None = None, timestamp: float | None = None) -> Event:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        if category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        with self._lock:
            event = Event(next(self._ids), self._clock() if timestamp is None else timestamp, level, category,
                          message, dict(data or {}))
            self._events.append(event)
            return event

    def since(self, after_id: int = 0, limit: int = 200) -> list[Event]:
        with self._lock:
            items = [e for e in self._events if e.id > after_id]
        return items[-limit:] if limit > 0 else items

    def latest(self, limit: int = 50) -> list[Event]:
        with self._lock:
            return list(self._events)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


def _pct_change(new: float, old: float) -> float:
    return abs(new - old) / max(abs(old), 1e-9)


def _error_components(record: DecisionRecord | None) -> dict[str, str]:
    if record is None:
        return {}
    out: dict[str, str] = {}
    for err in record.errors:
        component, _, detail = err.partition(":")
        out[component.strip()] = detail.strip()
    return out


def money(value: float | None, symbol: str) -> str:
    return "–" if value is None else f"{symbol}{value:,.2f}/hr"


def derive_events(previous: DecisionRecord | None, record: DecisionRecord, horizon_seconds: float | None = None,
                  deployment: str = "marketplace", currency_symbol: str = "₹") -> list[dict[str, Any]]:
    """Event specs (kwargs for :meth:`EventLog.emit`) describing what changed."""
    events: list[dict[str, Any]] = []
    ts = record.timestamp

    def add(message: str, level: str, category: str, **data: Any) -> None:
        events.append({"message": message, "level": level, "category": category, "data": data, "timestamp": ts})

    # errors appearing / clearing -------------------------------------------------------
    prev_err, cur_err = _error_components(previous), _error_components(record)
    for component, detail in cur_err.items():
        if component not in prev_err and component != "prediction":
            add(_ERROR_TEXT.get(component, "Error"), "error" if component != "metrics" else "warning",
                "system", component=component, detail=detail)
    for component in prev_err:
        if component not in cur_err and component != "prediction":
            add(_ERROR_TEXT.get(component, "Error").split(" - ")[0] + " - recovered", "success", "system",
                component=component)

    # traffic ----------------------------------------------------------------------------
    load = record.observed_load
    prev_load = previous.observed_load if previous else None
    if load is not None and prev_load is not None and _pct_change(load, prev_load) >= 0.25 and abs(load - prev_load) >= 3:
        if load > prev_load:
            add(f"Marketplace traffic increased to {load:.1f} req/s", "info", "traffic", load=load)
        else:
            add(f"Workload decreasing: {load:.1f} req/s", "info", "traffic", load=load)
    elif load is not None and prev_load is None:
        add(f"Marketplace traffic measured: {load:.1f} req/s", "info", "traffic", load=load)

    # forecast ---------------------------------------------------------------------------
    if record.fallback and not (previous and previous.fallback):
        add(f"Safe mode: {record.fallback_reason or 'forecast unavailable'} - planning on measured traffic, "
            "no scale-down", "warning", "forecast")
    if previous and previous.fallback and not record.fallback:
        add("Forecast model ready - predictive scaling active", "success", "forecast")
    peak = record.forecast_peak
    prev_peak = previous.forecast_peak if previous and not previous.fallback else None
    if peak is not None and not record.fallback and load is not None:
        rising = peak > max(load * 1.2, load + 3)
        if prev_peak is None or (_pct_change(peak, prev_peak) >= 0.25 and abs(peak - prev_peak) >= 4):
            horizon = f" within {horizon_seconds:.0f} s" if horizon_seconds else ""
            if rising:
                add(f"Future demand detected: LSTM forecasts {peak:.1f} req/s{horizon} (now {load:.1f})",
                    "info", "forecast", peak=peak, load=load)
            else:
                add(f"Forecast updated: {peak:.1f} req/s{horizon}", "info", "forecast", peak=peak)

    # capacity + cost ----------------------------------------------------------------------
    req = record.required_replicas
    prev_req = previous.required_replicas if previous else None
    if req is not None and req != prev_req:
        demand = record.effective_demand or 0.0
        add(f"Required pods: {req} (for {demand:.1f} req/s including the safety margin)", "info", "capacity",
            required=req, effective_demand=demand)
        if not record.budget_constrained and record.desired_replicas is not None:
            add(f"Cost checked: {record.desired_replicas} pods = {money(record.estimated_future_cost, currency_symbol)}"
                f" within the {money(record.max_budget, currency_symbol)} budget", "success", "cost",
                desired=record.desired_replicas, cost=record.estimated_future_cost)
    if record.budget_constrained and not (previous and previous.budget_constrained):
        add(f"Budget limit: demand needs {req} pods, the budget allows {record.max_affordable_replicas} - "
            f"limiting scaling to {record.desired_replicas}", "warning", "cost",
            required=req, selected=record.desired_replicas)
    if previous and previous.budget_constrained and not record.budget_constrained:
        add("Demand fits within the budget again", "success", "cost")

    # decisions and Kubernetes actions -------------------------------------------------------
    current, target = record.current_replicas, record.target_replicas
    if record.applied and current is not None and target is not None:
        verb = "up" if target > current else "down"
        add(f"Kubernetes scaling {verb} {current} → {target} pods", "info", "kubernetes",
            action=record.action, current=current, target=target)
        if target < current and record.estimated_future_cost is not None:
            add(f"Infrastructure cost reduced to {money(record.estimated_future_cost, currency_symbol)}",
                "success", "cost", cost=record.estimated_future_cost)
    elif record.action in {"SCALE_UP", "SCALE_DOWN"} and not record.applied and record.controller_mode == "observe":
        add(f"Decision {record.action} to {target} computed (observe mode - not applied)", "info", "decision")
    if (record.blocked_by in {"stabilization", "cooldown"} and record.desired_replicas is not None
            and current is not None and record.desired_replicas < current
            and (previous is None or previous.blocked_by != record.blocked_by)):
        add(f"Fewer pods would do ({record.desired_replicas}); waiting for the {record.blocked_by} window "
            "before scaling down", "info", "decision")

    # readiness -------------------------------------------------------------------------------
    if (previous is not None and record.ready_replicas is not None and current is not None
            and record.ready_replicas == current and current > 0
            and previous.ready_replicas is not None and previous.current_replicas is not None
            and previous.ready_replicas != record.ready_replicas):
        add(f"{record.ready_replicas}/{current} pods ready", "success", "kubernetes", ready=record.ready_replicas)
    return events
