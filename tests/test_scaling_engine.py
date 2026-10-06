from autoscaler.capacity_planner import CapacityPlanner
from autoscaler.cost_model import FlatRateCostModel
from autoscaler.cost_optimizer import Action, CostAwareOptimizer, OptimizerConstraints
from autoscaler.scaling_engine import ScalingEngine, ScalingPolicy


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def setup(policy=None):
    clock = Clock()
    engine = ScalingEngine(policy or ScalingPolicy(), clock=clock)
    opt = CostAwareOptimizer(FlatRateCostModel(5), OptimizerConstraints(1, 8, 100))
    plan = CapacityPlanner(20, 0.7, 0.15)
    return clock, engine, lambda load, cur: opt.optimize(plan.plan([load], sigma=0.5), cur)


def test_scale_up_is_step_limited_and_cooldown_applies():
    clock, engine, optimize = setup()
    d = engine.decide(optimize(100, 1), 1)
    assert d.should_apply and d.target_replicas == 5  # +4 max step
    engine.record_scale(Action.SCALE_UP, when=clock.t)
    clock.t += 5
    d = engine.decide(optimize(100, 5), 5)
    assert not d.should_apply and d.blocked_by == "cooldown"
    clock.t += 6
    d = engine.decide(optimize(100, 5), 5)
    assert d.should_apply and d.target_replicas == d.recommended_replicas > 5


def test_scale_down_waits_for_stabilisation_window():
    clock, engine, optimize = setup(ScalingPolicy(stabilization_window_seconds=60, scale_down_cooldown_seconds=0))
    engine.decide(optimize(60, 5), 5)  # recommendation 5
    clock.t += 30
    d = engine.decide(optimize(5, 5), 5)
    assert not d.should_apply and d.blocked_by == "stabilization"
    clock.t += 31
    d = engine.decide(optimize(5, 5), 5)
    assert d.should_apply and d.target_replicas == 2  # -3 max step


def test_safe_mode_never_scales_down():
    clock, engine, optimize = setup(ScalingPolicy(stabilization_window_seconds=0))
    d = engine.decide(optimize(1, 6), 6, fallback=True)
    assert not d.should_apply and d.blocked_by == "safe_mode"


def test_out_of_range_is_corrected_immediately():
    clock, engine, optimize = setup()
    d = engine.decide(optimize(1, 12), 12)
    assert d.should_apply and d.target_replicas == 8
