"""Capacity planning: *how many replicas does the predicted workload need?*

Knows nothing about cost or Kubernetes. Given a forecast for the next ``H``
intervals it computes

* the planning demand ``D_peak = max(current_load, max_h forecast_h)``,
* the effective demand with a safety margin ``D_eff = D_peak * (1 + s)``,
* the capacity of one replica at the target utilisation ``c = mu * u``,
* the required replicas ``r_req = ceil(D_eff / c)``.

Planning for the *peak of the horizon* (not the next value) is what makes the
autoscaler proactive: new pods need time to become ready.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class CapacityPlan:
    """Result of capacity planning for one control-loop iteration."""

    forecast: tuple[float, ...]
    current_load: float | None
    peak_demand: float
    effective_demand: float
    sigma: float
    capacity_per_replica: float
    required_replicas: int
    safety_margin: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "forecast": list(self.forecast),
            "current_load": self.current_load,
            "peak_demand": self.peak_demand,
            "effective_demand": self.effective_demand,
            "sigma": self.sigma,
            "capacity_per_replica": self.capacity_per_replica,
            "required_replicas": self.required_replicas,
            "safety_margin": self.safety_margin,
            "notes": list(self.notes),
        }


class CapacityPlanner:
    """Translate a workload forecast into a replica requirement."""

    def __init__(self, capacity_per_pod: float, target_utilization: float, safety_margin: float,
                 default_uncertainty_fraction: float = 0.10) -> None:
        if capacity_per_pod <= 0:
            raise ValueError("capacity_per_pod must be > 0")
        if not 0 < target_utilization <= 1:
            raise ValueError("target_utilization must be in (0, 1]")
        if safety_margin < 0:
            raise ValueError("safety_margin must be >= 0")
        self.capacity_per_pod = capacity_per_pod
        self.target_utilization = target_utilization
        self.safety_margin = safety_margin
        self.default_uncertainty_fraction = default_uncertainty_fraction

    @property
    def capacity_per_replica(self) -> float:
        """Requests/second one replica serves at the target utilisation (``mu * u``)."""
        return self.capacity_per_pod * self.target_utilization

    def replicas_for(self, demand: float) -> int:
        """Smallest replica count whose target-utilisation capacity covers ``demand``."""
        if demand <= 0:
            return 0
        return int(math.ceil(demand / self.capacity_per_replica - 1e-9))

    def plan(self, forecast: Sequence[float] | np.ndarray, current_load: float | None = None,
             sigma: float | None = None) -> CapacityPlan:
        """Capacity requirement for a forecast (``ValueError`` on invalid input)."""
        values = np.asarray(forecast, dtype=float).reshape(-1)
        if values.size == 0:
            raise ValueError("forecast must contain at least one value")
        if not np.all(np.isfinite(values)) or np.any(values < 0):
            raise ValueError("forecast must contain finite, non-negative values")
        notes: list[str] = []
        peak = float(values.max())
        if current_load is not None:
            if not math.isfinite(current_load) or current_load < 0:
                raise ValueError("current_load must be finite and non-negative")
            if current_load > peak:
                notes.append("current load exceeds forecast peak; planning for current load")
                peak = float(current_load)
        if sigma is None or not math.isfinite(sigma) or sigma < 0:
            sigma = self.default_uncertainty_fraction * peak
            notes.append("uncertainty defaulted to a fraction of peak demand")
        effective = peak * (1.0 + self.safety_margin)
        return CapacityPlan(
            forecast=tuple(float(v) for v in values),
            current_load=None if current_load is None else float(current_load),
            peak_demand=peak,
            effective_demand=effective,
            sigma=float(sigma),
            capacity_per_replica=self.capacity_per_replica,
            required_replicas=self.replicas_for(effective),
            safety_margin=self.safety_margin,
            notes=tuple(notes),
        )
