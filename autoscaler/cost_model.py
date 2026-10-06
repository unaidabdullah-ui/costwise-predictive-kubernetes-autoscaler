"""Replaceable infrastructure cost models.

The demo runs on Minikube, so there is **no real cloud bill**: cost is
*estimated* from the replica count with a configurable model. The optimiser only
depends on the :class:`CostModel` protocol, so a model backed by real
AWS/GCP/Azure prices can be dropped in without touching the optimiser.

``FlatRateCostModel``      ``cost_per_hour(r) = r * cost_per_replica``   (default)
``ResourceBasedCostModel`` price per replica from its CPU/memory *requests*
(``cpu * vcpu_hour_price + mem_gb * gb_hour_price``) - illustrative prices.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CostModel(Protocol):
    """Estimated cost of running ``replicas`` pods."""

    currency: str

    def hourly_cost(self, replicas: int) -> float: ...

    def cost_for(self, replicas: int, seconds: float) -> float: ...

    def describe(self) -> dict[str, object]: ...


class FlatRateCostModel:
    """Every replica costs the same fixed amount per hour."""

    def __init__(self, cost_per_replica_hour: float, currency: str = "INR") -> None:
        if cost_per_replica_hour < 0:
            raise ValueError("cost_per_replica_hour must be >= 0")
        self.cost_per_replica_hour = cost_per_replica_hour
        self.currency = currency

    def hourly_cost(self, replicas: int) -> float:
        if replicas < 0:
            raise ValueError("replicas must be >= 0")
        return replicas * self.cost_per_replica_hour

    def cost_for(self, replicas: int, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        return self.hourly_cost(replicas) * seconds / 3600.0

    def describe(self) -> dict[str, object]:
        return {
            "model": "flat",
            "currency": self.currency,
            "cost_per_replica_hour": self.cost_per_replica_hour,
            "formula": "pods × cost per pod-hour × hours",
            "label": "Estimated infrastructure cost (simulated prices, not a cloud bill)",
        }


class ResourceBasedCostModel(FlatRateCostModel):
    """Price a replica from its CPU and memory requests."""

    def __init__(self, cpu_cores: float, memory_gb: float, vcpu_hour_price: float, gb_hour_price: float,
                 currency: str = "INR") -> None:
        if min(cpu_cores, memory_gb, vcpu_hour_price, gb_hour_price) < 0:
            raise ValueError("resource quantities and prices must be >= 0")
        self.cpu_cores, self.memory_gb = cpu_cores, memory_gb
        self.vcpu_hour_price, self.gb_hour_price = vcpu_hour_price, gb_hour_price
        super().__init__(cpu_cores * vcpu_hour_price + memory_gb * gb_hour_price, currency)

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "model": "resource",
            "cpu_cores_per_replica": self.cpu_cores,
            "memory_gb_per_replica": self.memory_gb,
            "vcpu_hour_price": self.vcpu_hour_price,
            "gb_hour_price": self.gb_hour_price,
            "formula": "pods × (cpu cores × vCPU price + memory GB × GB price) × hours",
        }


def build_cost_model(kind: str, cost_per_replica: float, currency: str = "INR", cpu_cores: float = 0.25,
                     memory_gb: float = 0.125, vcpu_hour_price: float = 16.0,
                     gb_hour_price: float = 2.0) -> CostModel:
    """Factory used by the controller (``COST_MODEL=flat|resource``)."""
    if kind == "flat":
        return FlatRateCostModel(cost_per_replica, currency)
    if kind == "resource":
        return ResourceBasedCostModel(cpu_cores, memory_gb, vcpu_hour_price, gb_hour_price, currency)
    raise ValueError(f"Unknown cost model {kind!r}")
