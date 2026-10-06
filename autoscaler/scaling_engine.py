"""Kubernetes action policy: *what replica count should actually be applied now?*

The optimiser recommends an ideal count every interval; applying every
recommendation immediately would oscillate (5 → 8 → 5 → 8). This module adds the
safeguards production autoscalers use:

* **bounds** - target clamped to ``[min, max]`` (violations corrected even during cooldown);
* **minimum change** - ignore changes smaller than ``min_replica_change``;
* **step limits** - at most ``max_scale_up_step`` / ``max_scale_down_step`` pods per action;
* **cooldowns** - after any scaling action: short for scale-up, longer for scale-down;
* **scale-down stabilisation** - scale down only to the *highest* recommendation of
  the stabilisation window (like the HPA's ``stabilizationWindowSeconds``);
* **safe mode** - when the forecast is unavailable or not trusted, never scale down.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from autoscaler.cost_optimizer import Action, OptimizationResult

COOLDOWN_GRACE_SECONDS = 0.5  # absorbs scheduling jitter (loop period == cooldown)


@dataclass(frozen=True)
class ScalingPolicy:
    min_replicas: int = 1
    max_replicas: int = 8
    scale_up_cooldown_seconds: float = 10.0
    scale_down_cooldown_seconds: float = 20.0
    stabilization_window_seconds: float = 60.0
    min_replica_change: int = 1
    max_scale_up_step: int = 4
    max_scale_down_step: int = 3

    def validate(self) -> None:
        if self.min_replicas < 1 or self.max_replicas < self.min_replicas:
            raise ValueError("require 1 <= min_replicas <= max_replicas")
        if self.min_replica_change < 1:
            raise ValueError("min_replica_change must be >= 1")
        if self.max_scale_up_step < 1 or self.max_scale_down_step < 1:
            raise ValueError("step limits must be >= 1")


@dataclass(frozen=True)
class ScalingDecision:
    timestamp: float
    current_replicas: int
    recommended_replicas: int
    target_replicas: int
    action: Action
    should_apply: bool
    reason: str
    blocked_by: str | None = None
    cooldown_remaining_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "current_replicas": self.current_replicas,
            "recommended_replicas": self.recommended_replicas,
            "target_replicas": self.target_replicas,
            "action": self.action.value,
            "should_apply": self.should_apply,
            "reason": self.reason,
            "blocked_by": self.blocked_by,
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
        }


class ScalingEngine:
    """Stateful policy turning optimiser recommendations into safe actions."""

    def __init__(self, policy: ScalingPolicy, clock: Callable[[], float] = time.monotonic) -> None:
        policy.validate()
        self.policy = policy
        self.clock = clock
        self._recommendations: deque[tuple[float, int]] = deque()
        self._last_scale_time: float | None = None
        self._last_direction: Action | None = None

    def reset(self) -> None:
        """Forget cooldown and stabilisation history (demo reset)."""
        self._recommendations.clear()
        self._last_scale_time = None
        self._last_direction = None

    def record_scale(self, direction: Action, when: float | None = None) -> None:
        """Remember a *successful* scaling action (starts the cooldown)."""
        self._last_scale_time = self.clock() if when is None else when
        self._last_direction = direction

    def cooldown_remaining(self, direction: Action, now: float | None = None) -> float:
        if self._last_scale_time is None:
            return 0.0
        now = self.clock() if now is None else now
        cooldown = (self.policy.scale_up_cooldown_seconds if direction == Action.SCALE_UP
                    else self.policy.scale_down_cooldown_seconds)
        remaining = cooldown - (now - self._last_scale_time)
        return 0.0 if remaining <= COOLDOWN_GRACE_SECONDS else remaining

    def _stabilized(self, now: float, recommendation: int) -> int:
        window = self.policy.stabilization_window_seconds
        self._recommendations.append((now, recommendation))
        while self._recommendations and now - self._recommendations[0][0] > window:
            self._recommendations.popleft()
        return max(r for _, r in self._recommendations)

    def decide(self, optimization: OptimizationResult, current_replicas: int,
               fallback: bool = False) -> ScalingDecision:
        """Replica count to apply (or HOLD) for this iteration."""
        p = self.policy
        now = self.clock()
        recommended = optimization.desired_replicas
        budget_flag = optimization.budget_constrained or optimization.budget_infeasible

        def hold(reason: str, blocked_by: str | None, remaining: float = 0.0) -> ScalingDecision:
            return ScalingDecision(now, current_replicas, recommended, current_replicas,
                                   Action.BUDGET_LIMIT if budget_flag else Action.HOLD, False, reason,
                                   blocked_by, remaining)

        if current_replicas < p.min_replicas or current_replicas > p.max_replicas:
            target = min(max(current_replicas, p.min_replicas), p.max_replicas)
            self._stabilized(now, recommended)
            return ScalingDecision(
                now, current_replicas, recommended, target,
                Action.SCALE_UP if target > current_replicas else Action.SCALE_DOWN, True,
                f"current pods {current_replicas} outside the allowed range [{p.min_replicas}, {p.max_replicas}]",
            )

        stabilized = self._stabilized(now, recommended)
        target = min(max(recommended, p.min_replicas), p.max_replicas)
        if target < current_replicas:
            if fallback:
                return hold("safe mode: scale-down paused until the forecast is trusted again", "safe_mode")
            target = max(target, min(stabilized, current_replicas))
            if target >= current_replicas:
                return hold(
                    f"scale-down waits: highest recommendation in the last "
                    f"{p.stabilization_window_seconds:.0f} s is {stabilized} pods", "stabilization")

        delta = target - current_replicas
        if abs(delta) < p.min_replica_change:
            return hold("no change needed" if delta == 0 else f"change {delta:+d} below threshold",
                        None if delta == 0 else "threshold")

        direction = Action.SCALE_UP if delta > 0 else Action.SCALE_DOWN
        remaining = self.cooldown_remaining(direction, now)
        if remaining > 0:
            return hold(f"{direction.value} waits for the cooldown ({remaining:.0f} s left)", "cooldown", remaining)

        if direction == Action.SCALE_UP:
            target = min(target, current_replicas + p.max_scale_up_step)
        else:
            target = max(target, current_replicas - p.max_scale_down_step)
        action = Action.BUDGET_LIMIT if budget_flag else direction
        reason = f"{'scale up' if delta > 0 else 'scale down'} {current_replicas} → {target}"
        if budget_flag:
            reason += " (limited by budget)"
        return ScalingDecision(now, current_replicas, recommended, target, action, True, reason)
