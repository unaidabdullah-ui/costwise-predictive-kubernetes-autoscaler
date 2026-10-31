"""CostWise control plane end to end with a real store + traffic generator, a fake
Prometheus (fixed measurements) and the in-memory DryRunScaler."""

import shutil
import time

import numpy as np
import pytest
import requests

from autoscaler.config import AutoscalerConfig
from autoscaler.kubernetes_client import DryRunScaler
from autoscaler.metrics_source import MetricsUnavailableError, WorkloadSnapshot
from autoscaler.state import DecisionRecord
from costwise.explain import explain
from costwise.insights import comparison
from costwise.runtime import Runtime
from costwise.server import create_app
from costwise.settings import DashboardSettings
from marketplace.app import Settings
from marketplace.app import create_app as create_store
from ml.src.lite import DEFAULT_MODEL_PATH
from tests.conftest import serve
from workload_generator.app import create_app as create_generator
from workload_generator.engine import EngineSettings, TrafficEngine

H = {"X-CostWise": "1"}


class FakePrometheus:
    def __init__(self):
        self.load = 30.0
        self.up = True

    def snapshot(self, n):
        if not self.up:
            raise MetricsUnavailableError("down")
        return WorkloadSnapshot(time.time(), self.load, np.full(n, self.load), 40.0, 0.3, 2e8)

    def query_instant(self, q):
        if not self.up:
            raise MetricsUnavailableError("down")
        return 0.0


@pytest.fixture
def stack(tmp_path):
    model = tmp_path / "model" / "lstm_lite.npz"
    model.parent.mkdir()
    shutil.copy(DEFAULT_MODEL_PATH, model)
    shutil.copy(DEFAULT_MODEL_PATH.with_suffix(".json"), model.with_suffix(".json"))
    st = Settings()
    st.work_scale = 0.0
    with serve(create_store(st)) as store:
        eng = TrafficEngine(EngineSettings(target_url=store, workers=4))
        with serve(create_generator(eng, token="tok")) as gen:
            settings = DashboardSettings(state_dir=tmp_path / "state", generator_url=gen, generator_token="tok",
                                         marketplace_url=store, refresh_seconds=1.0)
            cfg = AutoscalerConfig(scaler_backend="dry-run", scaling_interval=1.0, model_path=str(model)).validate()
            prom = FakePrometheus()
            rt = Runtime(settings, cfg, scaler=DryRunScaler(1), metrics_source=prom,
                         retrain_args=("--quick",))
            with serve(create_app(rt)) as url:
                yield url, rt, prom


def wait_for(fn, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.25)
    return False


def test_faculty_demo_flow(stack):
    url, rt, prom = stack
    assert wait_for(lambda: requests.get(f"{url}/api/dashboard").json()["decision"] is not None)
    d = requests.get(f"{url}/api/dashboard").json()
    for key in ("health", "traffic", "metrics", "kubernetes", "forecast", "decision", "cost", "why",
                "comparison", "series", "events", "settings"):
        assert key in d
    assert d["cost"]["symbol"] == "₹" and "simulated" in d["cost"]["label"]
    assert d["kubernetes"]["backend"] == "dry-run"

    assert requests.post(f"{url}/demo/start", headers=H).json()["ok"]
    assert requests.get(f"{url}/api/dashboard").json()["traffic"]["scenario"] == "normal"
    r = requests.post(f"{url}/demo/flash-sale", headers=H).json()
    assert r["traffic"]["scenario"] == "flash_sale"
    prom.load = 70.0  # Prometheus now measures the rush
    assert wait_for(lambda: rt.scaler.get_replicas() >= 5)
    # the dashboard reads the cluster from a snapshot refreshed every refresh_seconds
    assert wait_for(lambda: requests.get(f"{url}/api/dashboard").json()["kubernetes"]["desired_replicas"] >= 5)
    d = requests.get(f"{url}/api/dashboard").json()
    assert d["kubernetes"]["desired_replicas"] >= 5
    assert any(e["category"] == "kubernetes" for e in d["events"])
    assert requests.post(f"{url}/demo/users", headers=H).json()["traffic"]["scenario"] == "users"
    assert requests.post(f"{url}/demo/traffic", json={"scenario": "busy"}, headers=H).json()["ok"]
    assert requests.post(f"{url}/demo/traffic", json={"scenario": "evil"}, headers=H).status_code == 400
    assert requests.post(f"{url}/demo/stop", headers=H).json()["ok"]

    r = requests.post(f"{url}/demo/reset", headers=H).json()
    assert r["ok"] and rt.scaler.get_replicas() == 1
    d = requests.get(f"{url}/api/dashboard").json()
    assert d["traffic"]["scenario"] == "stopped"
    assert [e["category"] for e in d["events"]][-1] == "demo"


def test_settings_are_validated(stack):
    url, rt, _ = stack
    r = requests.post(f"{url}/demo/settings", json={"max_budget": 20, "controller_mode": "observe"}, headers=H)
    assert r.json()["settings"]["max_budget"] == 20 and rt.config.controller_mode == "observe"
    assert requests.post(f"{url}/demo/settings", json={"max_budget": -5}, headers=H).status_code == 400
    assert requests.post(f"{url}/demo/settings", json={"prometheus_url": "http://evil"}, headers=H).status_code == 400
    assert rt.config.max_budget == 20


def test_security_guards(stack):
    url, *_ = stack
    assert requests.post(f"{url}/demo/reset").status_code == 403  # no X-CostWise header
    assert requests.post(f"{url}/demo/reset", headers={**H, "Origin": "http://evil.example"}).status_code == 403
    assert requests.get(f"{url}/api/dashboard", headers={"Host": "evil.example"}).status_code == 403
    page = requests.get(f"{url}/")
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    for path in ("/api/dashboard", "/api/technical"):
        assert "tok" not in requests.get(f"{url}{path}").text.replace("token", "")
    for path in ("/health", "/forecast", "/scaling-decision", "/cost", "/pods", "/events", "/metrics",
                 "/api/model", "/technical", "/architecture", "/static/app.js", "/static/chart.js"):
        assert requests.get(f"{url}{path}").status_code == 200, path


def test_prometheus_outage_is_visible_and_safe(stack):
    url, rt, prom = stack
    prom.up = False
    assert wait_for(lambda: requests.get(f"{url}/health").json()["components"]["prometheus"]["status"] == "critical")
    assert rt.state.last is None or rt.state.last.applied is False


def test_retrain_really_trains_and_reloads(stack):
    url, rt, _ = stack
    before = rt.predictor.info()["trained_at"]
    time.sleep(1.1)
    assert requests.post(f"{url}/model/retrain", headers=H).json()["retrain"]["status"] == "running"
    assert wait_for(lambda: rt.models.state["status"] in ("succeeded", "failed"), timeout=240)
    assert rt.models.state["status"] == "succeeded", rt.models.state
    assert rt.predictor.info()["trained_at"] != before


def rec(t, cur, tgt, load, ready, applied=True):
    return DecisionRecord(timestamp=t, iteration=int(t), controller_mode="active", optimizer_mode="cost_aware",
                          scaler_backend="dry-run", forecaster="LSTM", current_replicas=cur, target_replicas=tgt,
                          observed_load=load, ready_replicas=ready, applied=applied)


def test_comparison_measures_lead_time():
    records = [rec(0, 1, 3, 8.0, 1), rec(5, 3, 3, 10.0, 1, False), rec(10, 3, 3, 12.0, 3, False),
               rec(15, 3, 3, 15.0, 3, False)]
    c = comparison(records, 14.0)
    assert c["available"] and c["ahead_of_load"] == 1
    assert c["latest"]["lead_seconds"] == 15 and c["latest"]["startup_seconds"] == 10


def test_explanation_uses_real_numbers():
    r = rec(0, 2, 5, 31.5, 2)
    r.forecast_peak, r.required_replicas, r.desired_replicas = 63.0, 6, 5
    r.estimated_future_cost, r.max_budget, r.action = 25.0, 35.0, "SCALE_UP"
    r.inputs = {"currency_symbol": "₹", "capacity_per_replica": 14.0, "target_utilization": 0.7,
                "safety_margin": 0.15, "min_replicas": 1}
    w = explain(r, 30)
    text = " ".join(s["text"] for s in w["steps"])
    assert w["title"] == "Scaled up: 2 → 5 pods"
    assert "31.5" in text and "63.0" in text and "₹25.00" in text
