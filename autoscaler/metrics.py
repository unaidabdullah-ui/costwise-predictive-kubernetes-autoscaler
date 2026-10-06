"""Prometheus metrics of the autoscaler itself (``autoscaler_*``).

Built on the dependency-free :mod:`common.promtext` registry and exposed on the
control plane's ``/metrics`` endpoint. Label cardinality is bounded: ``action``
(4 values), ``direction`` (2), ``component`` (5) and static info labels.
"""

from __future__ import annotations

from typing import Any, Protocol

from autoscaler.state import DecisionRecord
from common.promtext import Registry

ERROR_COMPONENTS = ("metrics", "prediction", "optimizer", "kubernetes", "other")
ACTION_CODES = {"SCALE_UP": 1, "SCALE_DOWN": -1, "HOLD": 0, "BUDGET_LIMIT": 2}


class MetricsPublisher(Protocol):
    def publish(self, record: DecisionRecord) -> None: ...

    def set_model_info(self, info: dict[str, Any]) -> None: ...


class NullPublisher:
    """Publisher that does nothing (tests, simulations)."""

    def publish(self, record: DecisionRecord) -> None:
        return None

    def set_model_info(self, info: dict[str, Any]) -> None:
        return None


class PrometheusPublisher:
    """Maintain the ``autoscaler_*`` gauges, counters and histogram."""

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry if registry is not None else Registry()
        r = self.registry
        g = r.gauge
        self.prediction = g("autoscaler_prediction", "Predicted peak workload over the horizon (req/s)")
        self.prediction_next = g("autoscaler_prediction_next", "One-step-ahead workload forecast (req/s)")
        self.observed = g("autoscaler_observed_workload", "Observed workload (req/s)")
        self.effective_demand = g("autoscaler_effective_demand", "Planned demand incl. safety margin (req/s)")
        self.current_replicas = g("autoscaler_current_replicas", "Deployment spec.replicas before the decision")
        self.ready_replicas = g("autoscaler_ready_replicas", "Ready replicas of the target deployment")
        self.required_replicas = g("autoscaler_required_replicas", "Replicas required by capacity planning")
        self.desired_replicas = g("autoscaler_desired_replicas", "Replicas recommended by the cost optimiser")
        self.target_replicas = g("autoscaler_target_replicas", "Replicas after the scaling policy")
        self.estimated_cost = g("autoscaler_estimated_cost", "Estimated cost per hour of the current replicas")
        self.future_cost = g("autoscaler_estimated_future_cost", "Estimated cost per hour of the recommended replicas")
        self.budget = g("autoscaler_budget", "Configured budget (cost per hour)")
        self.budget_utilization = g("autoscaler_budget_utilization", "Estimated future cost / budget (ratio)")
        self.decision = g("autoscaler_decision", "Last decision: 1=SCALE_UP -1=SCALE_DOWN 0=HOLD 2=BUDGET_LIMIT")
        self.budget_constrained = g("autoscaler_budget_constrained", "1 if the budget limited scaling")
        self.capacity_shortfall = g("autoscaler_capacity_shortfall", "1 if planned capacity is below demand")
        self.fallback = g("autoscaler_fallback_active", "1 if safe mode (forecast unavailable or not trusted)")
        self.confidence = g("autoscaler_forecast_confidence", "Live forecast confidence (0-1)")
        self.forecast_mae = g("autoscaler_forecast_mae", "Rolling one-step forecast MAE (req/s)")
        self.forecast_rmse = g("autoscaler_forecast_rmse", "Rolling one-step forecast RMSE (req/s)")
        self.forecast_error = g("autoscaler_forecast_error", "Latest one-step forecast error (req/s)")
        self.model_test_mae = g("autoscaler_model_test_mae", "Offline test MAE of the loaded model (req/s)")
        self.model_test_rmse = g("autoscaler_model_test_rmse", "Offline test RMSE of the loaded model (req/s)")
        self.build_info = g("autoscaler_build_info", "Static configuration (value is always 1)",
                            ("controller_mode", "optimizer_mode", "forecaster", "scaler_backend"))
        self.decisions = r.counter("autoscaler_decisions", "Decisions taken, by action", ("action",))
        self.scaling_events = r.counter("autoscaler_scaling_events", "Scaling actions applied", ("direction",))
        self.errors = r.counter("autoscaler_errors", "Errors by component", ("component",))
        self.cost_accrued = r.counter("autoscaler_estimated_cost_accrued", "Estimated cost accrued since start")
        self.loop_duration = r.histogram("autoscaler_loop_duration_seconds", "Duration of one control-loop iteration",
                                         buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0))
        for action in ACTION_CODES:
            self.decisions.labels(action=action)
        for direction in ("up", "down"):
            self.scaling_events.labels(direction=direction)
        for component in ERROR_COMPONENTS:
            self.errors.labels(component=component)
        self._last_cumulative = 0.0

    @staticmethod
    def _set(gauge: Any, value: float | int | None) -> None:
        if value is not None:
            gauge.set(float(value))

    def set_model_info(self, info: dict[str, Any]) -> None:
        self._set(self.model_test_mae, info.get("test_mae"))
        self._set(self.model_test_rmse, info.get("test_rmse"))

    def publish(self, record: DecisionRecord) -> None:
        self.build_info.clear()
        self.build_info.labels(controller_mode=record.controller_mode, optimizer_mode=record.optimizer_mode,
                               forecaster=record.forecaster or "none", scaler_backend=record.scaler_backend).set(1)
        for gauge, value in (
            (self.prediction, record.peak_demand), (self.prediction_next, record.prediction_next),
            (self.observed, record.observed_load), (self.effective_demand, record.effective_demand),
            (self.current_replicas, record.current_replicas), (self.ready_replicas, record.ready_replicas),
            (self.required_replicas, record.required_replicas), (self.desired_replicas, record.desired_replicas),
            (self.target_replicas, record.target_replicas), (self.estimated_cost, record.estimated_current_cost),
            (self.future_cost, record.estimated_future_cost), (self.budget, record.max_budget),
            (self.budget_utilization, record.budget_utilization), (self.forecast_mae, record.forecast_mae),
            (self.forecast_rmse, record.forecast_rmse), (self.forecast_error, record.forecast_error),
            (self.confidence, record.forecast_confidence),
        ):
            self._set(gauge, value)
        action = record.action if record.action in ACTION_CODES else "HOLD"
        self.decision.set(ACTION_CODES[action])
        self.decisions.labels(action=action).inc()
        self.budget_constrained.set(1 if record.budget_constrained else 0)
        self.capacity_shortfall.set(1 if record.capacity_shortfall else 0)
        self.fallback.set(1 if record.fallback else 0)
        if record.applied and record.target_replicas is not None and record.current_replicas is not None:
            self.scaling_events.labels(
                direction="up" if record.target_replicas > record.current_replicas else "down").inc()
        for err in record.errors:
            component = err.split(":", 1)[0].strip()
            self.errors.labels(component=component if component in ERROR_COMPONENTS else "other").inc()
        accrued = record.cumulative_cost - self._last_cumulative
        if accrued > 0:
            self.cost_accrued.inc(accrued)
        self._last_cumulative = record.cumulative_cost
        self.loop_duration.observe(record.loop_seconds)

    def reset_cumulative(self) -> None:
        self._last_cumulative = 0.0

    def exposition(self) -> str:
        return self.registry.render()
