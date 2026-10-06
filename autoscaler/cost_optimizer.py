r"""Cost optimisation: *how many replicas can we afford while meeting predicted demand?*

Formulation (full derivation in ``docs/cost-optimization.md``)
-----------------------------------------------------------------

Decision variable: integer replica count ``r``.

Constraints::

    r_min <= r <= r_max                 (operational bounds)
    C(r) <= B                           (budget, cost per hour)
    r * mu * u >= D_eff                 (capacity / SLA; relaxed only if infeasible)

Objective (all terms in currency per hour)::

    J(r) = C(r) + lambda_sla * ES(r) + lambda_chg * |r - r_current|

``ES(r) = E[max(0, D - r*mu*u)]`` with ``D ~ Normal(D_peak, sigma^2)`` is the
expected demand above the target-utilisation capacity (closed form)::

    ES = (m - c) * Phi(z) + sigma * phi(z),   z = (m - c) / sigma

Solved **exactly by enumeration** (``r_max`` is small):

1. ``F = {r : r_min <= r <= r_max, C(r) <= B}``.
2. ``F`` empty → choose ``r_min`` (cheapest), flag ``budget_infeasible``.
3. ``S = {r in F : r >= r_req}`` non-empty → ``argmin_S J``.
4. otherwise best effort ``argmin_F J`` and flag ``capacity_shortfall`` (+
   ``budget_constrained`` when the budget, not ``r_max``, is binding).

Ties go to fewer replicas. ``PredictiveOptimizer`` is experiment Method C
(prediction without cost optimisation): ``r = clamp(r_req, r_min, r_max)``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from autoscaler.capacity_planner import CapacityPlan
from autoscaler.cost_model import CostModel

logger = logging.getLogger(__name__)


class Action(str, Enum):
    """Scaling actions reported by the optimiser and the scaling engine."""

    SCALE_UP = "SCALE_UP"
    SCALE_DOWN = "SCALE_DOWN"
    HOLD = "HOLD"
    BUDGET_LIMIT = "BUDGET_LIMIT"

    @property
    def code(self) -> int:
        """Numeric code exported as the ``autoscaler_decision`` gauge."""
        return {"SCALE_UP": 1, "SCALE_DOWN": -1, "HOLD": 0, "BUDGET_LIMIT": 2}[self.value]


class InvalidBudgetError(ValueError):
    """Raised for a non-positive or non-finite budget."""


@dataclass(frozen=True)
class OptimizerConstraints:
    min_replicas: int
    max_replicas: int
    max_budget: float  # currency per hour

    def validate(self) -> None:
        if self.min_replicas < 1 or self.max_replicas < self.min_replicas:
            raise ValueError("require 1 <= min_replicas <= max_replicas")
        if not math.isfinite(self.max_budget) or self.max_budget <= 0:
            raise InvalidBudgetError(f"max_budget must be a positive number, got {self.max_budget}")


@dataclass(frozen=True)
class OptimizerWeights:
    sla_penalty_per_rps: float = 2.0
    change_penalty: float = 0.2


@dataclass(frozen=True)
class CandidateEvaluation:
    replicas: int
    hourly_cost: float
    capacity_at_target: float
    expected_shortfall_rps: float
    objective: float
    within_budget: bool
    meets_capacity: bool


@dataclass(frozen=True)
class OptimizationResult:
    """Outcome of the optimisation for one iteration."""

    desired_replicas: int
    required_replicas: int
    current_replicas: int
    action: Action
    estimated_cost: float
    current_cost: float
    max_budget: float
    budget_utilization: float
    max_affordable_replicas: int
    expected_shortfall_rps: float
    objective: float
    budget_constrained: bool
    capacity_shortfall: bool
    budget_infeasible: bool
    reason: str
    mode: str = "cost_aware"
    candidates: tuple[CandidateEvaluation, ...] = field(default_factory=tuple)

    @property
    def budget_status(self) -> str:
        if self.budget_infeasible:
            return "INFEASIBLE"
        if self.estimated_cost > self.max_budget + 1e-12:
            return "EXCEEDED"
        if self.budget_constrained:
            return "CONSTRAINED"
        return "OK"

    @property
    def capacity_status(self) -> str:
        return "SHORTFALL" if self.capacity_shortfall else "SUFFICIENT"

    def to_dict(self, include_candidates: bool = False) -> dict[str, object]:
        data: dict[str, object] = {
            "desired_replicas": self.desired_replicas,
            "required_replicas": self.required_replicas,
            "current_replicas": self.current_replicas,
            "action": self.action.value,
            "estimated_cost": self.estimated_cost,
            "current_cost": self.current_cost,
            "max_budget": self.max_budget,
            "budget_utilization": self.budget_utilization,
            "max_affordable_replicas": self.max_affordable_replicas,
            "expected_shortfall_rps": self.expected_shortfall_rps,
            "objective": self.objective,
            "budget_constrained": self.budget_constrained,
            "capacity_shortfall": self.capacity_shortfall,
            "budget_infeasible": self.budget_infeasible,
            "budget_status": self.budget_status,
            "capacity_status": self.capacity_status,
            "reason": self.reason,
            "mode": self.mode,
        }
        if include_candidates:
            data["candidates"] = [c.__dict__ for c in self.candidates]
        return data


class ReplicaOptimizer(Protocol):
    mode: str

    def optimize(self, plan: CapacityPlan, current_replicas: int) -> OptimizationResult: ...


def _phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _Phi(z: float) -> float:  # noqa: N802 - mathematical notation
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def expected_shortfall(mean_demand: float, sigma: float, capacity: float) -> float:
    """``E[max(0, D - capacity)]`` for ``D ~ Normal(mean_demand, sigma^2)`` (req/s)."""
    if sigma <= 1e-9:
        return max(0.0, mean_demand - capacity)
    z = (mean_demand - capacity) / sigma
    return max(0.0, (mean_demand - capacity) * _Phi(z) + sigma * _phi(z))


def _direction(desired: int, current: int) -> Action:
    if desired > current:
        return Action.SCALE_UP
    if desired < current:
        return Action.SCALE_DOWN
    return Action.HOLD


def max_affordable_replicas(cost_model: CostModel, max_budget: float, upper: int) -> int:
    """Largest ``r <= upper`` with ``C(r) <= budget`` (0 if none)."""
    best = 0
    for r in range(0, upper + 1):
        if cost_model.hourly_cost(r) <= max_budget + 1e-12:
            best = r
        else:
            break
    return best


class CostAwareOptimizer:
    """Exact integer optimisation of cost + SLA risk under budget and bounds."""

    mode = "cost_aware"

    def __init__(self, cost_model: CostModel, constraints: OptimizerConstraints,
                 weights: OptimizerWeights | None = None) -> None:
        constraints.validate()
        self.cost_model = cost_model
        self.constraints = constraints
        self.weights = weights or OptimizerWeights()

    def evaluate(self, plan: CapacityPlan, replicas: int, current_replicas: int) -> CandidateEvaluation:
        cost = self.cost_model.hourly_cost(replicas)
        capacity = replicas * plan.capacity_per_replica
        shortfall = expected_shortfall(plan.peak_demand, plan.sigma, capacity)
        objective = (cost + self.weights.sla_penalty_per_rps * shortfall
                     + self.weights.change_penalty * abs(replicas - current_replicas))
        return CandidateEvaluation(replicas, cost, capacity, shortfall, objective,
                                   cost <= self.constraints.max_budget + 1e-12,
                                   replicas >= plan.required_replicas)

    def optimize(self, plan: CapacityPlan, current_replicas: int) -> OptimizationResult:
        if current_replicas < 0:
            raise ValueError("current_replicas must be >= 0")
        c = self.constraints
        affordable = max_affordable_replicas(self.cost_model, c.max_budget, c.max_replicas)
        current_cost = self.cost_model.hourly_cost(current_replicas)
        candidates = tuple(self.evaluate(plan, r, current_replicas)
                           for r in range(c.min_replicas, c.max_replicas + 1))
        feasible = [cand for cand in candidates if cand.within_budget]
        if not feasible:
            chosen = candidates[0]
            reason = (f"budget {c.max_budget:.2f}/h cannot pay for the minimum of {c.min_replicas} pod(s) "
                      f"({chosen.hourly_cost:.2f}/h); keeping the minimum")
            logger.warning("budget infeasible", extra={"reason": reason})
            return self._result(plan, chosen, current_replicas, current_cost, affordable, candidates,
                                budget_constrained=True, capacity_shortfall=not chosen.meets_capacity,
                                budget_infeasible=True, reason=reason)
        satisfying = [cand for cand in feasible if cand.meets_capacity]
        if satisfying:
            chosen = min(satisfying, key=lambda cand: (cand.objective, cand.replicas))
            reason = (f"cheapest pod count covering {plan.effective_demand:.1f} req/s "
                      f"(needs {plan.required_replicas}, budget allows {affordable})")
            return self._result(plan, chosen, current_replicas, current_cost, affordable, candidates,
                                budget_constrained=False, capacity_shortfall=False,
                                budget_infeasible=False, reason=reason)
        chosen = min(feasible, key=lambda cand: (cand.objective, cand.replicas))
        budget_binding = plan.required_replicas > affordable and affordable < c.max_replicas
        limit = "the budget" if budget_binding else "MAX_REPLICAS"
        reason = (f"demand needs {plan.required_replicas} pods but {limit} allows at most "
                  f"{min(affordable, c.max_replicas)}; best effort {chosen.replicas}")
        logger.warning("capacity shortfall", extra={"reason": reason, "budget_constrained": budget_binding})
        return self._result(plan, chosen, current_replicas, current_cost, affordable, candidates,
                            budget_constrained=budget_binding, capacity_shortfall=True,
                            budget_infeasible=False, reason=reason)

    def _result(self, plan: CapacityPlan, chosen: CandidateEvaluation, current: int, current_cost: float,
                affordable: int, candidates: tuple[CandidateEvaluation, ...], *, budget_constrained: bool,
                capacity_shortfall: bool, budget_infeasible: bool, reason: str) -> OptimizationResult:
        action = Action.BUDGET_LIMIT if budget_constrained else _direction(chosen.replicas, current)
        return OptimizationResult(
            desired_replicas=chosen.replicas,
            required_replicas=plan.required_replicas,
            current_replicas=current,
            action=action,
            estimated_cost=chosen.hourly_cost,
            current_cost=current_cost,
            max_budget=self.constraints.max_budget,
            budget_utilization=chosen.hourly_cost / self.constraints.max_budget,
            max_affordable_replicas=affordable,
            expected_shortfall_rps=chosen.expected_shortfall_rps,
            objective=chosen.objective,
            budget_constrained=budget_constrained,
            capacity_shortfall=capacity_shortfall,
            budget_infeasible=budget_infeasible,
            reason=reason,
            mode=self.mode,
            candidates=candidates,
        )


class PredictiveOptimizer:
    """Method C: replicas = clamp(required, bounds). The budget is only reported."""

    mode = "predictive"

    def __init__(self, cost_model: CostModel, constraints: OptimizerConstraints) -> None:
        constraints.validate()
        self.cost_model = cost_model
        self.constraints = constraints

    def optimize(self, plan: CapacityPlan, current_replicas: int) -> OptimizationResult:
        c = self.constraints
        desired = min(max(plan.required_replicas, c.min_replicas), c.max_replicas)
        cost = self.cost_model.hourly_cost(desired)
        capacity = desired * plan.capacity_per_replica
        return OptimizationResult(
            desired_replicas=desired,
            required_replicas=plan.required_replicas,
            current_replicas=current_replicas,
            action=_direction(desired, current_replicas),
            estimated_cost=cost,
            current_cost=self.cost_model.hourly_cost(current_replicas),
            max_budget=c.max_budget,
            budget_utilization=cost / c.max_budget,
            max_affordable_replicas=max_affordable_replicas(self.cost_model, c.max_budget, c.max_replicas),
            expected_shortfall_rps=expected_shortfall(plan.peak_demand, plan.sigma, capacity),
            objective=cost,
            budget_constrained=False,
            capacity_shortfall=desired < plan.required_replicas,
            budget_infeasible=False,
            reason="predictive mode: pods = required pods within bounds; budget not enforced",
            mode=self.mode,
        )


def main(argv: list[str] | None = None) -> int:
    """Explain one optimisation decision (every candidate).

        python -m autoscaler.cost_optimizer --forecast 50,58,66,72,76,78 --current 3 --load 45 --sigma 4
    """
    import argparse

    from autoscaler.capacity_planner import CapacityPlanner
    from autoscaler.config import AutoscalerConfig
    from autoscaler.cost_model import build_cost_model
    from common.env import load_dotenv
    from common.logging_utils import configure_logging

    load_dotenv()
    configure_logging("ERROR")
    parser = argparse.ArgumentParser(description="Evaluate the cost-aware optimiser for a forecast.")
    parser.add_argument("--forecast", required=True, help="comma-separated forecast (req/s)")
    parser.add_argument("--current", type=int, required=True, help="current pods")
    parser.add_argument("--load", type=float, default=None, help="current observed load (req/s)")
    parser.add_argument("--sigma", type=float, default=None, help="forecast RMSE (req/s)")
    parser.add_argument("--budget", type=float, default=None, help="override BUDGET_PER_HOUR")
    args = parser.parse_args(argv)
    cfg = AutoscalerConfig.from_env()
    if args.budget is not None:
        cfg = cfg.with_overrides(max_budget=args.budget)
    planner = CapacityPlanner(cfg.capacity_per_pod, cfg.target_utilization, cfg.safety_margin)
    plan = planner.plan([float(v) for v in args.forecast.split(",")], current_load=args.load, sigma=args.sigma)
    optimizer = CostAwareOptimizer(
        build_cost_model(cfg.cost_model, cfg.cost_per_replica, cfg.currency, cfg.pod_cpu_request_cores,
                         cfg.pod_memory_request_gb, cfg.vcpu_hour_price, cfg.gb_hour_price),
        OptimizerConstraints(cfg.min_replicas, cfg.max_replicas, cfg.max_budget),
        OptimizerWeights(cfg.sla_penalty_per_rps, cfg.change_penalty),
    )
    result = optimizer.optimize(plan, args.current)
    sym = cfg.currency_symbol
    print(f"D_peak={plan.peak_demand:.2f}  D_eff={plan.effective_demand:.2f}  "
          f"capacity/pod={plan.capacity_per_replica:.2f}  r_req={plan.required_replicas}  sigma={plan.sigma:.2f}")
    print(f"{'pods':>4} {'cost/h':>9} {'capacity':>9} {'ES(r)':>8} {'J(r)':>9}  budget  demand")
    for c in result.candidates:
        marker = "  <== chosen" if c.replicas == result.desired_replicas else ""
        print(f"{c.replicas:>4} {sym}{c.hourly_cost:>7.2f} {c.capacity_at_target:>9.1f} "
              f"{c.expected_shortfall_rps:>8.2f} {c.objective:>9.3f}  {'ok' if c.within_budget else 'OVER':<6}  "
              f"{'ok' if c.meets_capacity else 'short'}{marker}")
    print(f"\ndesired={result.desired_replicas} action={result.action.value} budget={result.budget_status} "
          f"capacity={result.capacity_status}\nreason: {result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
