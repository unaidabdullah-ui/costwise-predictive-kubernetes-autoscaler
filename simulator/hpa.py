"""Emulation of the Kubernetes Horizontal Pod Autoscaler (reactive baseline).

Implements the documented HPA algorithm (autoscaling/v2 defaults) so the reactive
baseline can be compared with CostWise in the offline simulator. Live
experiments use the real HPA (``kubernetes/experiments/hpa-baseline.yaml``).

    ratio   = current_metric / target_metric
    desired = ceil(current_replicas * ratio)   if |ratio - 1| > tolerance, else current

Defaults reproduced: 15 s sync, 10 % tolerance, scale-up limited to
max(4 pods, 100 %) per period, 300 s scale-down stabilisation.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class HPAConfig:
    min_replicas: int = 1
    max_replicas: int = 8
    target_utilization: float = 0.70
    tolerance: float = 0.10
    sync_period_seconds: float = 15.0
    metric_window_seconds: float = 30.0
    scale_down_stabilization_seconds: float = 300.0
    scale_up_max_pods: int = 4
    scale_up_max_percent: float = 100.0


class HPAEmulator:
    """Stateful reactive recommender following the HPA algorithm."""

    def __init__(self, config: HPAConfig) -> None:
        if not 0 < config.target_utilization <= 1:
            raise ValueError("target_utilization must be in (0, 1]")
        self.config = config
        self._recommendations: deque[tuple[float, int]] = deque()

    def recommend(self, now: float, current_replicas: int, utilization: float | None) -> int:
        """Replica count the HPA would set at ``now`` (``None`` metrics → keep current)."""
        c = self.config
        if utilization is None or current_replicas == 0:
            return max(current_replicas, c.min_replicas)
        ratio = utilization / c.target_utilization
        raw = current_replicas if abs(ratio - 1.0) <= c.tolerance else math.ceil(current_replicas * ratio - 1e-9)
        raw = min(max(raw, c.min_replicas), c.max_replicas)
        self._recommendations.append((now, raw))
        while self._recommendations and now - self._recommendations[0][0] > c.scale_down_stabilization_seconds:
            self._recommendations.popleft()
        desired = raw
        if raw < current_replicas:
            desired = min(current_replicas, max(r for _, r in self._recommendations))
        if desired > current_replicas:
            limit = max(current_replicas + c.scale_up_max_pods,
                        math.floor(current_replicas * (1 + c.scale_up_max_percent / 100.0)))
            desired = min(desired, limit)
        return min(max(desired, c.min_replicas), c.max_replicas)
