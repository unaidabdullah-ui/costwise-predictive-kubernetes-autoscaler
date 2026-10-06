"""Plain-English explanations of controller decisions ("Why did the system scale?").

Everything is computed from the real :class:`~autoscaler.state.DecisionRecord`
the controller produced - no canned text with made-up numbers.
"""

from __future__ import annotations

import math
from typing import Any

from autoscaler.state import DecisionRecord

BLOCK_TEXT = {
    "cooldown": "a cooldown is active after the last change (prevents flapping)",
    "stabilization": "the stabilisation window keeps the higher pod count for a while (prevents flapping)",
    "threshold": "the change would be too small to be worth it",
    "safe_mode": "safe mode never removes pods while the forecast is not trusted",
}


def _n(v: float | None, digits: int = 1) -> str:
    return "—" if v is None or not math.isfinite(v) else f"{v:.{digits}f}"


def _pods(n: int | None) -> str:
    return "—" if n is None else f"{n} pod" + ("" if n == 1 else "s")


def headline(record: DecisionRecord) -> tuple[str, str]:
    """(short title, tone) for the decision card."""
    cur, tgt = record.current_replicas, record.target_replicas
    if cur is None:
        return "Waiting for Kubernetes", "warning"
    if record.errors and record.observed_load is None:
        return "Holding: no measurements", "warning"
    budget = " (budget limit)" if record.action == "BUDGET_LIMIT" else ""
    if tgt is not None and tgt > cur:
        verb = "Scaled up" if record.applied else "Would scale up"
        return f"{verb}: {cur} → {tgt} pods{budget}", "up"
    if tgt is not None and tgt < cur:
        verb = "Scaled down" if record.applied else "Would scale down"
        return f"{verb}: {cur} → {tgt} pods{budget}", "down"
    if budget:
        return f"Holding at {_pods(cur)} - budget limit reached", "warning"
    return f"Holding at {_pods(cur)}", "hold"


def explain(record: DecisionRecord | None, horizon_seconds: float) -> dict[str, Any]:
    """Structured explanation: title, tone, ordered reasoning steps, and the guard that applied."""
    if record is None:
        return {"title": "Starting up", "tone": "hold", "steps": [
            {"icon": "clock", "text": "The control loop has not finished its first decision yet."}],
            "guard": None, "mode_note": None}
    title, tone = headline(record)
    inputs = record.inputs or {}
    sym = inputs.get("currency_symbol", "")
    per_pod = inputs.get("capacity_per_replica")
    steps: list[dict[str, str]] = []
    if record.observed_load is not None:
        steps.append({"icon": "traffic",
                      "text": f"The store is receiving {_n(record.observed_load)} requests per second right now "
                              f"(measured by Prometheus)."})
    if record.forecast_peak is not None and not record.fallback:
        steps.append({"icon": "forecast",
                      "text": f"The LSTM predicts up to {_n(record.forecast_peak)} req/s within the next "
                              f"{horizon_seconds:.0f} seconds."})
    elif record.fallback:
        steps.append({"icon": "shield",
                      "text": f"Safe mode: {record.fallback_reason or 'forecast not available'}. "
                              f"Planning uses the measured load instead, and pods are never removed."})
    if per_pod and record.required_replicas is not None:
        margin = inputs.get("safety_margin")
        steps.append({"icon": "pods",
                      "text": f"One pod should handle about {_n(per_pod)} req/s (at "
                              f"{(inputs.get('target_utilization') or 0) * 100:.0f}% of its capacity), so with a "
                              f"{(margin or 0) * 100:.0f}% safety margin the store needs "
                              f"{_pods(max(record.required_replicas, inputs.get('min_replicas') or 1))}"
                              f"{' (the minimum)' if record.required_replicas < (inputs.get('min_replicas') or 1) else ''}."})
    if record.desired_replicas is not None and record.estimated_future_cost is not None:
        budget = record.max_budget
        text = (f"{_pods(record.desired_replicas)} cost {sym}{_n(record.estimated_future_cost, 2)} per hour "
                f"(budget {sym}{_n(budget, 2)} per hour).")
        if record.budget_constrained:
            text += (f" The budget only allows {_pods(record.max_affordable_replicas)}, so CostWise stays within "
                     f"budget and reports a capacity shortfall.")
        elif record.desired_replicas < (record.required_replicas or 0):
            text += " The cost optimiser judged the extra pod not worth its cost for this short peak."
        steps.append({"icon": "cost", "text": text})
    guard = None
    if record.blocked_by:
        guard = BLOCK_TEXT.get(record.blocked_by, record.blocked_by)
        steps.append({"icon": "guard", "text": f"Not changed yet because {guard}."})
    changing = record.target_replicas is not None and record.target_replicas != record.current_replicas
    if changing:
        if record.applied:
            steps.append({"icon": "k8s", "text": f"CostWise asked Kubernetes to run {_pods(record.target_replicas)} "
                                                 f"(Deployment scale patched)."})
        elif record.controller_mode == "observe":
            steps.append({"icon": "eye", "text": "Observe mode: the decision was NOT applied to Kubernetes."})
    elif record.current_replicas is not None and not record.blocked_by and record.desired_replicas is not None:
        steps.append({"icon": "check", "text": f"{_pods(record.current_replicas)} already match the plan - "
                                               f"no change needed."})
    for err in record.errors:
        steps.append({"icon": "error", "text": err})
    return {"title": title, "tone": tone, "steps": steps, "guard": guard,
            "mode_note": "Observe mode - decisions are not applied" if record.controller_mode == "observe" else None}
