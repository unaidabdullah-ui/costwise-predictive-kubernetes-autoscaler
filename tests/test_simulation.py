"""The offline experiment harness runs the production controller code on a simulated cluster."""

import pytest

pd = pytest.importorskip("pandas")

from autoscaler.config import AutoscalerConfig  # noqa: E402
from experiments.analysis import summarize  # noqa: E402
from ml.src.forecasters import load_forecaster  # noqa: E402
from simulator.cluster_simulator import SimulationConfig, run_simulation  # noqa: E402
from simulator.scenarios import get_scenario  # noqa: E402


@pytest.fixture(scope="module")
def results():
    cfg = AutoscalerConfig().validate()
    fc = load_forecaster("lstm_lite", None, 6, 12)
    sim = SimulationConfig(seed=7, warmup_seconds=60)
    scen = get_scenario("flash_sale")
    out = {}
    for method in ("fixed", "hpa", "cost_aware"):
        res = run_simulation(scen, method, cfg, sim, (lambda: fc) if method == "cost_aware" else None)
        out[method] = summarize(res.timeseries, scenario="flash_sale", method=method,
                                capacity_per_pod=cfg.capacity_per_pod, target_utilization=cfg.target_utilization,
                                interval_s=cfg.scaling_interval)
    return out


def test_all_methods_produce_summaries(results):
    for m, s in results.items():
        assert s["total_requests"] > 0 and s["avg_replicas"] >= 1, m


def test_cost_aware_is_cheaper_than_fixed_peak_provisioning(results):
    assert results["cost_aware"]["estimated_cost"] < results["fixed"]["estimated_cost"]


def test_cost_aware_stays_within_budget(results):
    assert results["cost_aware"]["budget_utilization_peak"] <= 1.0 + 1e-9
