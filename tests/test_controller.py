"""Controller loop with fakes: no Prometheus, no cluster (DryRunScaler)."""

import numpy as np

from autoscaler.config import AutoscalerConfig
from autoscaler.controller import build_controller
from autoscaler.events import EventLog
from autoscaler.kubernetes_client import DryRunScaler, ScalerError
from autoscaler.metrics_source import MetricsUnavailableError, WorkloadSnapshot
from autoscaler.predictor_client import PredictionUnavailableError


class FakeMetrics:
    def __init__(self, load=10.0):
        self.load = load
        self.fail = False

    def snapshot(self, history_points):
        if self.fail:
            raise MetricsUnavailableError("down")
        return WorkloadSnapshot(0.0, self.load, np.full(history_points, self.load), 50.0, 0.2, 1e8)


class FakePredictor:
    horizon = 6
    sequence_length = 12
    uncertainty = 1.0
    available = True

    def __init__(self, value=None):
        self.value = value
        self.fail = False

    def predict(self, history):
        if self.fail:
            raise PredictionUnavailableError("model missing")
        v = self.value if self.value is not None else history[-1]
        return np.full(6, float(v))

    def info(self):
        return {"name": "fake", "label": "Fake", "available": True}


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def make(load=10.0, predicted=None, **overrides):
    cfg = AutoscalerConfig(scaler_backend="dry-run", **overrides).validate()
    scaler = DryRunScaler(1)
    metrics, predictor, events = FakeMetrics(load), FakePredictor(predicted), EventLog()
    ctl = build_controller(cfg, predictor=predictor, scaler=scaler, metrics_source=metrics, events=events)
    ctl.clock = Clock()
    return ctl, scaler, metrics, predictor, events


def test_scales_up_ahead_of_a_predicted_peak():
    ctl, scaler, *_ = make(load=10, predicted=50)
    rec = ctl.run_once()
    assert rec.applied and rec.action == "SCALE_UP"
    assert scaler.get_replicas() == 5  # 1 + max step 4; 57.5 req/s needs 5 pods
    assert rec.required_replicas == 5 and rec.estimated_future_cost == 25


def test_budget_is_never_exceeded():
    ctl, scaler, *_ = make(load=10, predicted=200, max_budget=15.0)
    for _ in range(5):
        ctl.clock.t += 30
        rec = ctl.run_once()
    assert scaler.get_replicas() == 3 and rec.budget_constrained


def test_observe_mode_does_not_apply():
    ctl, scaler, *_ = make(load=10, predicted=80, controller_mode="observe")
    rec = ctl.run_once()
    assert not rec.applied and scaler.get_replicas() == 1
    assert "observe mode" in rec.reason


def test_metrics_outage_holds():
    ctl, scaler, metrics, *_ = make()
    metrics.fail = True
    rec = ctl.run_once()
    assert rec.action == "HOLD" and not rec.applied and any(e.startswith("metrics") for e in rec.errors)


def test_kubernetes_outage_holds():
    ctl, scaler, *_ = make()

    def boom():
        raise ScalerError("unreachable")

    scaler.get_status = boom
    rec = ctl.run_once()
    assert not rec.applied and rec.current_replicas is None


def test_safe_mode_plans_on_measured_load_and_never_scales_down():
    ctl, scaler, metrics, predictor, events = make(load=60)
    predictor.fail = True
    scaler.scale(8)
    metrics.load = 5
    for _ in range(4):
        ctl.clock.t += 60
        rec = ctl.run_once()
    assert rec.fallback and scaler.get_replicas() == 8
    metrics.load = 80
    ctl.clock.t += 60
    rec = ctl.run_once()
    assert rec.fallback and rec.required_replicas == 7  # 92 req/s / 14 -> 7 (scale-up still allowed)
    assert any("Safe mode" in e.message for e in events.latest())


def test_reset_clears_state_and_cost():
    ctl, *_ = make(load=10, predicted=30)
    ctl.run_once()
    ctl.clock.t += 3600
    rec = ctl.run_once()
    assert rec.cumulative_cost > 0
    ctl.reset()
    assert ctl.state.last is None


def test_apply_overrides_validates():
    ctl, *_ = make()
    ctl.apply_overrides(max_budget=20.0)
    assert ctl.config.max_budget == 20.0
    try:
        ctl.apply_overrides(max_budget=-1.0)
    except Exception as exc:  # noqa: BLE001
        assert "BUDGET" in str(exc)
    else:
        raise AssertionError("negative budget accepted")
    assert ctl.config.max_budget == 20.0
