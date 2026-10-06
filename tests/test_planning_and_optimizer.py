import math

import pytest

from autoscaler.capacity_planner import CapacityPlanner
from autoscaler.cost_model import FlatRateCostModel, build_cost_model
from autoscaler.cost_optimizer import (
    Action,
    CostAwareOptimizer,
    InvalidBudgetError,
    OptimizerConstraints,
    OptimizerWeights,
    PredictiveOptimizer,
    expected_shortfall,
)


def planner():
    return CapacityPlanner(20.0, 0.7, 0.15)


def test_required_replicas_use_target_utilisation_and_margin():
    plan = planner().plan([10, 30, 50], current_load=12, sigma=2.0)
    assert plan.peak_demand == 50
    assert plan.effective_demand == pytest.approx(57.5)
    assert plan.required_replicas == math.ceil(57.5 / 14)  # 5


def test_current_load_above_forecast_is_respected():
    plan = planner().plan([5, 5], current_load=40, sigma=1.0)
    assert plan.peak_demand == 40
    assert plan.notes


@pytest.mark.parametrize("bad", [[], [float("nan")], [-1.0]])
def test_invalid_forecast_rejected(bad):
    with pytest.raises(ValueError):
        planner().plan(bad)


def test_expected_shortfall_closed_form():
    assert expected_shortfall(10, 0, 20) == 0
    assert expected_shortfall(30, 0, 20) == 10
    assert expected_shortfall(20, 5, 20) == pytest.approx(5 / math.sqrt(2 * math.pi), rel=1e-6)


def make_opt(budget=35.0, max_r=8):
    return CostAwareOptimizer(FlatRateCostModel(5.0), OptimizerConstraints(1, max_r, budget), OptimizerWeights(2.0, 0.2))


def test_cost_aware_picks_cheapest_count_that_covers_demand():
    res = make_opt().optimize(planner().plan([40], sigma=1.0), current_replicas=1)
    assert res.desired_replicas == res.required_replicas == 4  # 46 req/s / 14
    assert res.action == Action.SCALE_UP
    assert res.estimated_cost == 20 and not res.budget_constrained


def test_budget_caps_scaling_and_is_reported():
    res = make_opt(budget=15).optimize(planner().plan([80], sigma=1.0), current_replicas=2)
    assert res.desired_replicas == 3  # ₹15 buys 3 pods
    assert res.budget_constrained and res.capacity_shortfall
    assert res.action == Action.BUDGET_LIMIT
    assert res.estimated_cost <= 15


def test_infeasible_budget_keeps_minimum():
    res = make_opt(budget=1).optimize(planner().plan([5], sigma=1.0), current_replicas=1)
    assert res.budget_infeasible and res.desired_replicas == 1


def test_non_positive_budget_rejected():
    with pytest.raises(InvalidBudgetError):
        make_opt(budget=0)


def test_predictive_optimizer_ignores_cost_but_respects_bounds():
    opt = PredictiveOptimizer(FlatRateCostModel(5.0), OptimizerConstraints(1, 8, 1000))
    res = opt.optimize(planner().plan([200], sigma=1.0), current_replicas=1)
    assert res.desired_replicas == 8 and res.capacity_shortfall


def test_resource_cost_model():
    m = build_cost_model("resource", 0, cpu_cores=0.25, memory_gb=0.125, vcpu_hour_price=16, gb_hour_price=2)
    assert m.hourly_cost(2) == pytest.approx(2 * (4 + 0.25))
    assert m.cost_for(2, 1800) == pytest.approx(m.hourly_cost(2) / 2)
