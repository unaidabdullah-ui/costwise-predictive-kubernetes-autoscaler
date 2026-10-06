"""The predictive, cost-aware autoscaling control loop.

Each iteration (every ``SCALING_INTERVAL`` seconds, 5 s by default)::

    read Deployment replicas           (kubernetes_client)
    collect metrics + history          (metrics_source / Prometheus)
    check forecast confidence          (forecast_tracker)
    forecast the next H intervals      (predictor_client / LSTM)
    plan required capacity             (capacity_planner)
    optimise cost under the budget     (cost_optimizer)
    apply the stability policy         (scaling_engine)
    patch Deployment.spec.replicas     (kubernetes_client, when allowed)
    publish metrics, events, history   (metrics, events, state)

Failure handling - never make a dangerous decision on bad data:

* Kubernetes unreadable       → HOLD (nothing can be applied anyway).
* Prometheus unavailable      → HOLD, no forecast.
* forecast missing / invalid / warming up / not trusted (live error too high)
                              → **safe mode**: plan on the measured load, never scale down.
* invalid budget              → rejected at start-up by config validation.
* any unexpected exception    → logged, counted, the loop continues.

    python -m autoscaler.controller --dry-run --once     # one iteration, no cluster
"""

from __future__ import annotations

import argparse
import csv
import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from autoscaler.capacity_planner import CapacityPlanner
from autoscaler.config import AutoscalerConfig
from autoscaler.cost_model import CostModel, build_cost_model
from autoscaler.cost_optimizer import (
    Action,
    CostAwareOptimizer,
    InvalidBudgetError,
    OptimizerConstraints,
    OptimizerWeights,
    PredictiveOptimizer,
    ReplicaOptimizer,
)
from autoscaler.events import EventLog, derive_events
from autoscaler.forecast_tracker import ForecastTracker
from autoscaler.kubernetes_client import DryRunScaler, KubernetesScaler, ReplicaScaler, ScalerError
from autoscaler.metrics import MetricsPublisher, NullPublisher
from autoscaler.metrics_source import MetricsUnavailableError, PrometheusMetricsSource, WorkloadSnapshot
from autoscaler.predictor_client import PredictionUnavailableError, PredictorClient, build_local_predictor
from autoscaler.scaling_engine import ScalingEngine, ScalingPolicy
from autoscaler.state import ControllerState, DecisionRecord
from common.env import load_dotenv
from common.logging_utils import configure_logging

logger = logging.getLogger(__name__)

HISTORY_COLUMNS = ("timestamp", "request_rate", "latency_ms", "cpu_cores", "memory_bytes", "replicas",
                   "prediction_next", "target_replicas", "action")


class MetricsSource(Protocol):
    def snapshot(self, history_points: int) -> WorkloadSnapshot: ...


class AutoscalerController:
    """Orchestrates prediction → planning → optimisation → Kubernetes action."""

    OVERRIDABLE = ("max_budget", "min_replicas", "max_replicas", "target_utilization", "safety_margin",
                   "cost_per_replica", "optimizer_mode", "controller_mode")

    def __init__(self, config: AutoscalerConfig, metrics_source: MetricsSource, predictor: PredictorClient,
                 planner: CapacityPlanner, optimizer: ReplicaOptimizer, engine: ScalingEngine,
                 scaler: ReplicaScaler, cost_model: CostModel, publisher: MetricsPublisher | None = None,
                 state: ControllerState | None = None, tracker: ForecastTracker | None = None,
                 clock: Callable[[], float] = time.time, feature_warmup: int = 6,
                 events: EventLog | None = None) -> None:
        self.config = config
        self.metrics_source = metrics_source
        self.predictor = predictor
        self.planner = planner
        self.optimizer = optimizer
        self.engine = engine
        self.scaler = scaler
        self.cost_model = cost_model
        self.publisher = publisher or NullPublisher()
        self.state = state or ControllerState()
        self.tracker = tracker or ForecastTracker(config.scaling_interval)
        self.clock = clock
        self.feature_warmup = feature_warmup
        self.events = events
        self._lock = threading.RLock()
        self._iteration = 0
        self._cumulative_cost = 0.0
        self._last_tick: float | None = None
        self._last_replicas: int | None = None
        self._history_path = Path(config.history_csv) if config.history_csv else None
        self.publisher.set_model_info(predictor.info())

    # --------------------------------------------------------------- helpers
    def _base_record(self, now: float) -> DecisionRecord:
        info = self.predictor.info()
        return DecisionRecord(
            timestamp=now, iteration=self._iteration, controller_mode=self.config.controller_mode,
            optimizer_mode=self.config.optimizer_mode, scaler_backend=getattr(self.scaler, "backend", "unknown"),
            forecaster=info.get("label") or info.get("name"), max_budget=self.config.max_budget,
        )

    def inputs_snapshot(self) -> dict[str, Any]:
        """Optimiser inputs shown next to every decision (explainability)."""
        cfg = self.config
        return {
            "capacity_per_pod": cfg.capacity_per_pod,
            "target_utilization": cfg.target_utilization,
            "safety_margin": cfg.safety_margin,
            "capacity_per_replica": cfg.replica_capacity_at_target,
            "min_replicas": cfg.min_replicas,
            "max_replicas": cfg.max_replicas,
            "cost_per_replica_hour": self.cost_model.hourly_cost(1),
            "max_budget": cfg.max_budget,
            "currency": cfg.currency,
            "currency_symbol": cfg.currency_symbol,
            "optimizer_mode": cfg.optimizer_mode,
            "horizon_steps": self.predictor.horizon,
            "interval_seconds": cfg.scaling_interval,
            "horizon_seconds": self.predictor.horizon * cfg.scaling_interval,
            "sla_penalty_per_rps": cfg.sla_penalty_per_rps,
            "change_penalty": cfg.change_penalty,
            "min_forecast_confidence": cfg.min_forecast_confidence,
        }

    def _accrue_cost(self, now: float, replicas: int | None) -> None:
        if self._last_tick is not None and self._last_replicas is not None:
            self._cumulative_cost += self.cost_model.cost_for(self._last_replicas, max(0.0, now - self._last_tick))
        self._last_tick = now
        if replicas is not None:
            self._last_replicas = replicas

    def _prepare_history(self, snapshot: WorkloadSnapshot) -> tuple[np.ndarray, str | None]:
        """Pad short histories (just after start-up) once at least half is available."""
        need = self.predictor.sequence_length
        hist = np.asarray(snapshot.history, dtype=float)
        if hist.size >= need:
            return hist, None
        if hist.size >= max(2, need // 2):
            return np.concatenate([np.full(need - hist.size, hist[0]), hist]), \
                f"history padded ({hist.size}/{need} points)"
        raise PredictionUnavailableError(
            f"warming up: {hist.size * self.config.scaling_interval:.0f} s of traffic history, "
            f"the LSTM needs {need * self.config.scaling_interval:.0f} s")

    # ------------------------------------------------------------- iteration
    def run_once(self) -> DecisionRecord:
        """Execute one full control-loop iteration and return its record."""
        with self._lock:
            started = time.perf_counter()
            now = self.clock()
            self._iteration += 1
            record = self._base_record(now)
            previous = self.state.last
            try:
                self._iterate(record, now)
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                logger.exception("Unexpected controller error")
                record.errors.append(f"other: {exc}")
                record.action = Action.HOLD.value
                record.applied = False
                record.reason = "unexpected error; holding"
            record.cumulative_cost = self._cumulative_cost
            record.loop_seconds = time.perf_counter() - started
            self.state.add(record)
            self.publisher.publish(record)
            self._log(record)
            self._append_history(record)
            self._emit_events(previous, record)
            return record

    def _emit_events(self, previous: DecisionRecord | None, record: DecisionRecord) -> None:
        if self.events is None:
            return
        try:
            for spec in derive_events(previous, record, self.predictor.horizon * self.config.scaling_interval,
                                      self.config.target_deployment, self.config.currency_symbol):
                self.events.emit(**spec)
        except Exception:  # noqa: BLE001 - events are best effort
            logger.exception("event derivation failed")

    def _iterate(self, record: DecisionRecord, now: float) -> None:
        cfg = self.config
        record.inputs = self.inputs_snapshot()
        # 1) Kubernetes state --------------------------------------------------------
        try:
            status = self.scaler.get_status()
        except ScalerError as exc:
            record.errors.append(f"kubernetes: {exc}")
            record.reason = "cannot read the deployment; holding"
            self._accrue_cost(now, None)
            return
        current = status.spec_replicas
        record.current_replicas = current
        record.ready_replicas = status.ready_replicas
        record.estimated_current_cost = self.cost_model.hourly_cost(current)
        self._accrue_cost(now, current)

        # 2) metrics -----------------------------------------------------------------
        try:
            snapshot = self.metrics_source.snapshot(self.predictor.sequence_length + self.feature_warmup)
        except MetricsUnavailableError as exc:
            record.errors.append(f"metrics: {exc}")
            record.target_replicas = current
            record.reason = "metrics unavailable; holding the current pods"
            return
        record.observed_load = snapshot.request_rate
        record.latency_p95_ms = snapshot.latency_p95_ms
        record.cpu_cores = snapshot.cpu_cores
        record.memory_bytes = snapshot.memory_bytes
        matured = self.tracker.observe(now, snapshot.request_rate)
        one_step = [m for m in matured if m.steps_ahead == 1]
        if one_step:
            record.forecast_error = one_step[-1].error
        record.forecast_mae = self.tracker.mae(1)
        record.forecast_rmse = self.tracker.rmse(1)
        record.forecast_confidence = self.tracker.confidence(min_samples=cfg.confidence_min_samples,
                                                             tolerance=cfg.replica_capacity_at_target)

        # 3) prediction (with safe mode) ------------------------------------------------
        notes: list[str] = []
        forecast: np.ndarray | None = None
        try:
            history, pad_note = self._prepare_history(snapshot)
            if pad_note:
                notes.append(pad_note)
            forecast = np.asarray(self.predictor.predict(history), dtype=float)
            if forecast.size == 0 or not np.all(np.isfinite(forecast)) or np.any(forecast < 0):
                raise PredictionUnavailableError("forecast contained invalid values")
            record.forecast = [float(v) for v in forecast]
            record.forecast_peak = float(forecast.max())
            conf = record.forecast_confidence
            if conf is not None and conf < cfg.min_forecast_confidence:
                raise PredictionUnavailableError(
                    f"forecast confidence {conf:.0%} below {cfg.min_forecast_confidence:.0%} (live error too high)")
        except PredictionUnavailableError as exc:
            record.fallback = True
            record.fallback_reason = str(exc)
            if not str(exc).startswith("warming up"):
                record.errors.append(f"prediction: {exc}")
            # an untrusted forecast stays visible on the chart, but planning uses the measured load
            planning = np.full(max(1, self.predictor.horizon), snapshot.request_rate)
        else:
            planning = forecast
        record.prediction_next = float(record.forecast[0]) if record.forecast else None

        # 4) capacity planning -------------------------------------------------------------
        sigma = self.tracker.worst_rmse() or self.predictor.uncertainty
        plan = self.planner.plan(planning, current_load=snapshot.request_rate, sigma=sigma)
        record.peak_demand = plan.peak_demand
        record.effective_demand = plan.effective_demand
        record.sigma = plan.sigma
        record.required_replicas = plan.required_replicas

        # 5) cost optimisation ----------------------------------------------------------------
        try:
            opt = self.optimizer.optimize(plan, current)
        except (InvalidBudgetError, ValueError) as exc:
            record.errors.append(f"optimizer: {exc}")
            record.target_replicas = current
            record.reason = "optimisation failed; holding"
            return
        record.desired_replicas = opt.desired_replicas
        record.estimated_future_cost = opt.estimated_cost
        record.budget_utilization = opt.budget_utilization
        record.budget_constrained = opt.budget_constrained
        record.capacity_shortfall = opt.capacity_shortfall
        record.budget_infeasible = opt.budget_infeasible
        record.budget_status = opt.budget_status
        record.capacity_status = opt.capacity_status
        record.max_affordable_replicas = opt.max_affordable_replicas
        record.optimizer_reason = opt.reason
        record.optimizer_candidates = [
            {"replicas": c.replicas, "hourly_cost": c.hourly_cost, "capacity": c.capacity_at_target,
             "expected_shortfall": c.expected_shortfall_rps, "objective": c.objective,
             "within_budget": c.within_budget, "meets_capacity": c.meets_capacity}
            for c in opt.candidates
        ]

        # 6) scaling policy -------------------------------------------------------------------
        decision = self.engine.decide(opt, current, fallback=record.fallback)
        record.target_replicas = decision.target_replicas
        record.action = decision.action.value
        record.blocked_by = decision.blocked_by
        record.decision_reason = decision.reason
        record.expected_capacity = decision.target_replicas * plan.capacity_per_replica
        reason_parts = [decision.reason, opt.reason, *notes]
        if record.fallback:
            reason_parts.append("SAFE MODE: planning on the measured load")

        # 7) Kubernetes action ------------------------------------------------------------------
        if decision.should_apply:
            if cfg.controller_mode == "observe":
                reason_parts.append("observe mode: decision NOT applied")
            else:
                try:
                    self.scaler.scale(decision.target_replicas)
                    record.applied = True
                    direction = Action.SCALE_UP if decision.target_replicas > current else Action.SCALE_DOWN
                    self.engine.record_scale(direction, when=decision.timestamp)
                    self._last_replicas = decision.target_replicas
                except ScalerError as exc:
                    record.errors.append(f"kubernetes: {exc}")
                    reason_parts.append("the scaling call failed")
        record.reason = " | ".join(p for p in reason_parts if p)
        if record.forecast:  # also while untrusted, so the model can earn trust back
            self.tracker.register(now, record.forecast, baseline=snapshot.request_rate)

    # ------------------------------------------------------------ overrides
    def apply_overrides(self, **changes: Any) -> AutoscalerConfig:
        """Change selected settings at runtime (validated) and rebuild dependent parts."""
        unknown = set(changes) - set(self.OVERRIDABLE)
        if unknown:
            raise ValueError(f"cannot override {sorted(unknown)}")
        with self._lock:
            cfg = self.config.with_overrides(**changes)
            cost_model = build_cost_model_from_config(cfg)
            self.config = cfg
            self.cost_model = cost_model
            self.optimizer = build_optimizer(cfg, cost_model)
            self.planner = CapacityPlanner(cfg.capacity_per_pod, cfg.target_utilization, cfg.safety_margin,
                                           cfg.default_uncertainty_fraction)
            self.engine.policy = build_scaling_policy(cfg)
            self.engine.policy.validate()
            logger.info("settings changed", extra={"changes": changes})
            return cfg

    def reset(self) -> None:
        """Forget decisions, forecast errors, cooldowns and accrued cost (demo reset)."""
        with self._lock:
            self.state.clear()
            self.tracker.reset()
            self.engine.reset()
            self._cumulative_cost = 0.0
            self._last_tick = None
            self._last_replicas = None
            reset_cumulative = getattr(self.publisher, "reset_cumulative", None)
            if reset_cumulative:
                reset_cumulative()

    # --------------------------------------------------------------- outputs
    def _log(self, record: DecisionRecord) -> None:
        logger.log(
            logging.WARNING if record.errors else logging.INFO,
            "decision",
            extra={
                "action": record.action, "applied": record.applied, "pods": record.current_replicas,
                "target": record.target_replicas,
                "load_rps": None if record.observed_load is None else round(record.observed_load, 1),
                "forecast_peak": None if record.forecast_peak is None else round(record.forecast_peak, 1),
                "safe_mode": record.fallback, "reason": record.reason[:160],
            },
        )

    def _append_history(self, record: DecisionRecord) -> None:
        """Persist measurements for retraining (``HISTORY_CSV``)."""
        if self._history_path is None or record.observed_load is None:
            return
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self._history_path.exists()
            with self._history_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if new_file:
                    writer.writerow(HISTORY_COLUMNS)
                writer.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.timestamp)),
                    round(record.observed_load, 4), record.latency_p95_ms, record.cpu_cores, record.memory_bytes,
                    record.current_replicas, record.prediction_next, record.target_replicas, record.action,
                ])
        except OSError as exc:
            logger.warning("could not append history", extra={"error": str(exc)})

    # ------------------------------------------------------------------- loop
    def run_forever(self, stop_event: threading.Event) -> None:
        """Run iterations every ``scaling_interval`` seconds until ``stop_event`` is set."""
        self.state.running = True
        logger.info("controller started", extra={"interval_s": self.config.scaling_interval,
                                                 "mode": self.config.controller_mode})
        try:
            while not stop_event.is_set():
                tick = time.monotonic()
                self.run_once()
                stop_event.wait(max(0.0, self.config.scaling_interval - (time.monotonic() - tick)))
        finally:
            self.state.running = False
            logger.info("controller stopped")


# ------------------------------------------------------------------------ factory
def build_optimizer(cfg: AutoscalerConfig, cost_model: CostModel) -> ReplicaOptimizer:
    constraints = OptimizerConstraints(cfg.min_replicas, cfg.max_replicas, cfg.max_budget)
    if cfg.optimizer_mode == "predictive":
        return PredictiveOptimizer(cost_model, constraints)
    return CostAwareOptimizer(cost_model, constraints, OptimizerWeights(cfg.sla_penalty_per_rps, cfg.change_penalty))


def build_cost_model_from_config(cfg: AutoscalerConfig) -> CostModel:
    return build_cost_model(cfg.cost_model, cfg.cost_per_replica, cfg.currency, cpu_cores=cfg.pod_cpu_request_cores,
                            memory_gb=cfg.pod_memory_request_gb, vcpu_hour_price=cfg.vcpu_hour_price,
                            gb_hour_price=cfg.gb_hour_price)


def build_scaling_policy(cfg: AutoscalerConfig) -> ScalingPolicy:
    return ScalingPolicy(
        min_replicas=cfg.min_replicas, max_replicas=cfg.max_replicas,
        scale_up_cooldown_seconds=cfg.scale_up_cooldown_seconds, scale_down_cooldown_seconds=cfg.cooldown_seconds,
        stabilization_window_seconds=cfg.stabilization_window_seconds, min_replica_change=cfg.min_replica_change,
        max_scale_up_step=cfg.max_scale_up_step, max_scale_down_step=cfg.max_scale_down_step,
    )


def build_metrics_source(cfg: AutoscalerConfig) -> PrometheusMetricsSource:
    return PrometheusMetricsSource(cfg.prometheus_url, cfg.workload_query, cfg.latency_query, cfg.cpu_query,
                                   cfg.memory_query, timeout_seconds=cfg.prometheus_timeout_seconds,
                                   bearer_token=cfg.prometheus_bearer_token, step_seconds=cfg.scaling_interval)


def build_scaler(cfg: AutoscalerConfig) -> ReplicaScaler:
    if cfg.scaler_backend == "dry-run":
        return DryRunScaler(cfg.min_replicas, cfg.target_deployment)
    return KubernetesScaler(cfg.kubernetes_namespace, cfg.target_deployment, retries=cfg.kubernetes_retries,
                            backoff_seconds=cfg.kubernetes_retry_backoff_seconds, kube_context=cfg.kube_context or None)


def build_controller(cfg: AutoscalerConfig, publisher: MetricsPublisher | None = None,
                     state: ControllerState | None = None, predictor: PredictorClient | None = None,
                     events: EventLog | None = None, scaler: ReplicaScaler | None = None,
                     metrics_source: MetricsSource | None = None) -> AutoscalerController:
    """Wire all components from configuration (dependency-injection root)."""
    predictor = predictor or build_local_predictor(cfg.forecaster, Path(cfg.model_path) if cfg.model_path else None,
                                                   cfg.prediction_horizon, cfg.sequence_length)
    model_step = predictor.info().get("step_seconds")
    if model_step and abs(float(model_step) - cfg.scaling_interval) > 1e-6:
        logger.warning("SCALING_INTERVAL differs from the model's training step; forecasts may be less accurate",
                       extra={"scaling_interval": cfg.scaling_interval, "model_step_seconds": model_step})
    cost_model = build_cost_model_from_config(cfg)
    return AutoscalerController(
        config=cfg, metrics_source=metrics_source or build_metrics_source(cfg), predictor=predictor,
        planner=CapacityPlanner(cfg.capacity_per_pod, cfg.target_utilization, cfg.safety_margin,
                                cfg.default_uncertainty_fraction),
        optimizer=build_optimizer(cfg, cost_model), engine=ScalingEngine(build_scaling_policy(cfg)),
        scaler=scaler or build_scaler(cfg), cost_model=cost_model, publisher=publisher, state=state,
        tracker=ForecastTracker(cfg.scaling_interval), events=events,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the control loop without the dashboard (headless)."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the cost-aware predictive autoscaler loop (headless).")
    parser.add_argument("--once", action="store_true", help="run a single iteration and exit")
    parser.add_argument("--dry-run", action="store_true", help="do not touch Kubernetes (in-memory scaler)")
    parser.add_argument("--observe", action="store_true", help="compute decisions but never apply them")
    parser.add_argument("--forecaster", choices=("lstm_lite", "lstm", "holt", "naive", "moving_average"))
    parser.add_argument("--optimizer", choices=("cost_aware", "predictive"))
    parser.add_argument("--interval", type=float)
    args = parser.parse_args(argv)
    cfg = AutoscalerConfig.from_env()
    overrides: dict[str, Any] = {}
    if args.dry_run:
        overrides["scaler_backend"] = "dry-run"
    if args.observe:
        overrides["controller_mode"] = "observe"
    if args.forecaster:
        overrides["forecaster"] = args.forecaster
    if args.optimizer:
        overrides["optimizer_mode"] = args.optimizer
    if args.interval:
        overrides["scaling_interval"] = args.interval
    if overrides:
        cfg = cfg.with_overrides(**overrides)
    configure_logging(cfg.log_level, cfg.log_format)
    controller = build_controller(cfg)
    if args.once:
        record = controller.run_once()
        print({k: v for k, v in record.to_dict().items() if k not in ("optimizer_candidates", "inputs")})
        return 1 if record.errors and record.current_replicas is None else 0
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    controller.run_forever(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
