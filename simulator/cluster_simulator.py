"""Discrete-time simulator of the marketplace Deployment under a rate scenario.

Purpose: reproducible comparisons of scaling methods in seconds, with identical
traffic for every method:

* ``fixed``       - A: a fixed number of pods provisioned for the scenario's peak,
* ``hpa``         - B: reactive HPA emulation (:mod:`simulator.hpa`),
* ``predictive``  - C: the production controller with ``PredictiveOptimizer``,
* ``cost_aware``  - D: the production controller with ``CostAwareOptimizer`` (CostWise).

The predictive methods run the **production code path**
(:class:`autoscaler.controller.AutoscalerController`, capacity planner,
optimiser, scaling engine). Only the environment is simulated:
:class:`SimulatedCluster` stands in for the Kubernetes API and
:class:`SimulatedMetricsSource` for Prometheus; time is virtual.

Cluster model (``docs/experiments.md``):

* a requested pod serves traffic ``pod_startup_seconds`` after it is requested;
  removed pods disappear immediately;
* capacity = ready pods × ``capacity_per_pod``;
* latency = M/M/1-style curve + backlog while overloaded; backlog beyond
  ``max_queue_seconds`` is dropped (failed requests);
* the autoscaler observes ``rate()[30s]`` (30 s trailing mean); the HPA observes
  average CPU of ready pods over 30 s, capped at 100 % of the limit;
* every requested (starting or ready) pod is billed;
* a warm-up (default 2 min) at the scenario's initial load precedes every run so
  all methods start in steady state and the forecaster has a full history window.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from autoscaler.capacity_planner import CapacityPlanner
from autoscaler.config import AutoscalerConfig
from autoscaler.controller import (
    AutoscalerController,
    build_cost_model_from_config,
    build_optimizer,
    build_scaling_policy,
)
from autoscaler.forecast_tracker import ForecastTracker
from autoscaler.kubernetes_client import DeploymentStatus
from autoscaler.metrics_source import WorkloadSnapshot
from autoscaler.predictor_client import LocalPredictor
from autoscaler.scaling_engine import ScalingEngine
from common import system_model as sm
from common.patterns import trailing_mean
from ml.src.forecasters import Forecaster
from simulator.hpa import HPAConfig, HPAEmulator
from simulator.scenarios import Scenario

METHODS: tuple[str, ...] = ("fixed", "hpa", "predictive", "cost_aware")
METHOD_LABELS = {
    "fixed": "A: Fixed pods (peak)",
    "hpa": "B: Reactive HPA",
    "predictive": "C: Predictive (LSTM)",
    "cost_aware": "D: Predictive + cost-aware",
}


@dataclass(frozen=True)
class SimulationConfig:
    step_seconds: float = 1.0
    pod_startup_seconds: float = 12.0
    base_latency_ms: float = 25.0
    sla_latency_ms: float = 250.0
    max_queue_seconds: float = 5.0
    rate_window_seconds: float = 30.0
    warmup_seconds: float = 120.0
    seed: int = 42
    scale: float = 1.0


class VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SimulatedCluster:
    """Stand-in for the Kubernetes Deployment (implements ``ReplicaScaler``)."""

    backend = "simulated"

    def __init__(self, initial_replicas: int, clock: VirtualClock, startup_seconds: float) -> None:
        self.clock = clock
        self.startup = startup_seconds
        self._pods: list[float] = [-math.inf] * initial_replicas  # ready-at times
        self.scale_events = 0

    @property
    def spec_replicas(self) -> int:
        return len(self._pods)

    @property
    def ready_replicas(self) -> int:
        now = self.clock()
        return sum(1 for t in self._pods if t <= now)

    def get_status(self) -> DeploymentStatus:
        ready = self.ready_replicas
        return DeploymentStatus("sim", "sim", self.spec_replicas, ready, ready, self.spec_replicas,
                                self.spec_replicas - ready)

    def get_replicas(self) -> int:
        return self.spec_replicas

    def scale(self, replicas: int) -> None:
        if replicas < 0:
            raise ValueError("replicas must be >= 0")
        current = self.spec_replicas
        if replicas == current:
            return
        self.scale_events += 1
        if replicas > current:
            self._pods.extend([self.clock() + self.startup] * (replicas - current))
        else:
            self._pods.sort()  # Kubernetes prefers deleting not-ready (newest) pods first
            self._pods = self._pods[:replicas]


class SimulatedMetricsSource:
    """Stand-in for Prometheus: ``rate()[30s]``-style observed load + history."""

    def __init__(self, sim: ClusterSimulation) -> None:
        self.sim = sim

    def snapshot(self, history_points: int) -> WorkloadSnapshot:
        interval = self.sim.controller_interval
        now = self.sim.clock()
        times = [now - k * interval for k in range(history_points - 1, -1, -1)]
        hist = np.array([self.sim.observed_load_at(t) for t in times if t >= 0])
        current = self.sim.observed_load_at(now)
        return WorkloadSnapshot(now, current, hist if hist.size else np.array([current]),
                                latency_p95_ms=self.sim.last_latency_ms)


@dataclass
class SimulationResult:
    scenario: str
    method: str
    timeseries: Any  # pandas.DataFrame
    config: dict[str, Any] = field(default_factory=dict)


class ClusterSimulation:
    """Run one scenario under one scaling method."""

    def __init__(self, scenario: Scenario, method: str, autoscaler_config: AutoscalerConfig,
                 sim_config: SimulationConfig | None = None,
                 forecaster_factory: Callable[[], Forecaster] | None = None) -> None:
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")
        if method in ("predictive", "cost_aware") and forecaster_factory is None:
            raise ValueError("predictive methods need a forecaster_factory")
        self.scenario = scenario
        self.method = method
        self.cfg = autoscaler_config.with_overrides(
            optimizer_mode="predictive" if method == "predictive" else "cost_aware", controller_mode="active")
        self.sim = sim_config or SimulationConfig()
        self.clock = VirtualClock()
        self.controller_interval = self.cfg.scaling_interval
        dt = self.sim.step_seconds
        profile = scenario.profile(step_s=dt, seed=self.sim.seed, scale=self.sim.scale)
        warm = int(round(self.sim.warmup_seconds / dt))
        self.offset = warm * dt
        self.load = np.concatenate([np.full(warm, profile[0]), profile])
        self.times = np.arange(len(self.load)) * dt
        window = max(1, int(round(self.sim.rate_window_seconds / dt)))
        self.observed = trailing_mean(self.load, window)
        planner = CapacityPlanner(self.cfg.capacity_per_pod, self.cfg.target_utilization, self.cfg.safety_margin)
        if method == "fixed":
            initial = planner.replicas_for(float(profile.max()) * (1 + self.cfg.safety_margin))
        else:
            initial = planner.replicas_for(float(self.load[0]))
        initial = min(max(initial, self.cfg.min_replicas), self.cfg.max_replicas)
        self.fixed_replicas = initial if method == "fixed" else None
        self.cluster = SimulatedCluster(initial, self.clock, self.sim.pod_startup_seconds)
        self.cost_model = build_cost_model_from_config(self.cfg)
        self.last_latency_ms: float | None = None
        self._forecaster_factory = forecaster_factory
        self.controller: AutoscalerController | None = None
        self.hpa: HPAEmulator | None = None
        if method == "hpa":
            self.hpa = HPAEmulator(HPAConfig(min_replicas=self.cfg.min_replicas, max_replicas=self.cfg.max_replicas,
                                             target_utilization=self.cfg.target_utilization))
        elif method in ("predictive", "cost_aware"):
            self.controller = self._build_controller()

    def _build_controller(self) -> AutoscalerController:
        assert self._forecaster_factory is not None
        predictor = LocalPredictor(self._forecaster_factory, default_horizon=self.cfg.prediction_horizon,
                                   default_sequence_length=self.cfg.sequence_length, clock=self.clock)
        return AutoscalerController(
            config=self.cfg, metrics_source=SimulatedMetricsSource(self), predictor=predictor,
            planner=CapacityPlanner(self.cfg.capacity_per_pod, self.cfg.target_utilization,
                                    self.cfg.safety_margin, self.cfg.default_uncertainty_fraction),
            optimizer=build_optimizer(self.cfg, self.cost_model),
            engine=ScalingEngine(build_scaling_policy(self.cfg), clock=self.clock),
            scaler=self.cluster, cost_model=self.cost_model, tracker=ForecastTracker(self.controller_interval),
            clock=self.clock,
        )

    def observed_load_at(self, t: float) -> float:
        i = int(min(len(self.observed) - 1, max(0, round(t / self.sim.step_seconds))))
        return float(self.observed[i])

    def _hpa_utilization(self, t: float) -> float | None:
        ready = self.cluster.ready_replicas
        if ready == 0:
            return None
        return min(self.observed_load_at(t) / (ready * self.cfg.capacity_per_pod), 1.0)

    def run(self) -> SimulationResult:
        import pandas as pd

        dt = self.sim.step_seconds
        backlog = 0.0
        cumulative_cost = 0.0
        rows: list[dict[str, Any]] = []
        next_control = 0.0
        pred_next = pred_peak = desired = np.nan
        action = "HOLD"
        safe_mode = 0
        for i, t in enumerate(self.times):
            self.clock.now = float(t)
            if t + 1e-9 >= next_control and self.method != "fixed":
                if self.hpa is not None:
                    rec = self.hpa.recommend(t, self.cluster.spec_replicas, self._hpa_utilization(t))
                    cur = self.cluster.spec_replicas
                    action = "SCALE_UP" if rec > cur else "SCALE_DOWN" if rec < cur else "HOLD"
                    self.cluster.scale(rec)
                    desired = rec
                    next_control += HPAConfig().sync_period_seconds
                else:
                    assert self.controller is not None
                    record = self.controller.run_once()
                    pred_next = record.prediction_next if record.prediction_next is not None else np.nan
                    pred_peak = record.forecast_peak if record.forecast_peak is not None else np.nan
                    desired = record.desired_replicas if record.desired_replicas is not None else np.nan
                    action = record.action
                    safe_mode = int(record.fallback)
                    next_control += self.controller_interval
            load = float(self.load[i])
            ready = self.cluster.ready_replicas
            capacity = ready * self.cfg.capacity_per_pod
            rho = load / capacity if capacity > 0 else math.inf
            if capacity > 0:
                backlog = max(0.0, backlog + (load - capacity) * dt / capacity)
            else:
                backlog = self.sim.max_queue_seconds + 1.0
            dropped = 0.0
            if backlog > self.sim.max_queue_seconds:
                dropped = (backlog - self.sim.max_queue_seconds) * max(capacity, 1.0)
                backlog = self.sim.max_queue_seconds
            latency = float(sm.latency_ms(min(rho, 10.0), self.sim.base_latency_ms, backlog))
            self.last_latency_ms = latency
            if t < self.offset - 1e-9:
                continue
            cost_rate = self.cost_model.hourly_cost(self.cluster.spec_replicas)
            cumulative_cost += cost_rate * dt / 3600.0
            rows.append({
                "time_s": t - self.offset, "load_rps": load, "observed_rps": self.observed_load_at(t),
                "spec_replicas": self.cluster.spec_replicas, "ready_replicas": ready,
                "utilization": rho if math.isfinite(rho) else np.nan, "latency_ms": latency,
                "sla_violation": int(latency > self.sim.sla_latency_ms or dropped > 0),
                "dropped_requests": dropped, "cost_rate": cost_rate, "cumulative_cost": cumulative_cost,
                "budget": self.cfg.max_budget, "prediction_next": pred_next, "prediction_peak": pred_peak,
                "desired_replicas": desired, "action": action, "safe_mode": safe_mode,
            })
        return SimulationResult(self.scenario.name, self.method, pd.DataFrame(rows), {
            "simulation": self.sim.__dict__, "autoscaler": self.cfg.public_dict(),
            "scale_events": self.cluster.scale_events, "fixed_replicas": self.fixed_replicas,
        })


def run_simulation(scenario: Scenario, method: str, autoscaler_config: AutoscalerConfig,
                   sim_config: SimulationConfig | None = None,
                   forecaster_factory: Callable[[], Forecaster] | None = None) -> SimulationResult:
    return ClusterSimulation(scenario, method, autoscaler_config, sim_config, forecaster_factory).run()
