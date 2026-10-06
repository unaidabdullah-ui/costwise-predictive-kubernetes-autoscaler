# Cost-aware optimisation

## Cost model

* **Flat** (default): cost(r) = r × `COST_PER_REPLICA_HOUR` (₹5) per hour.
* **Resource**: cost(r) = r × (CPU request × vCPU price + memory request × GB price),
  `COST_MODEL=resource`, `VCPU_HOUR_PRICE`, `GB_HOUR_PRICE`.

Prices are simulated to make the trade-off visible; the dashboard labels them so.
Accrued cost integrates cost(r) over the time each pod count was active.

## Objective

```
J(r) = cost(r) + λ_SLA · ES(r) + λ_chg · |r − r_current|
ES(r) = E[max(0, Λ − r·μ·u)],  Λ ~ Normal(peak, σ²)
      = (peak − C)·Φ(z) + σ·φ(z),  z = (peak − C)/σ,  C = r·μ·u
```

λ_SLA = 2 (₹ per req/s of expected shortfall), λ_chg = 0.2 (₹ per pod changed).

## Decision rule

1. Candidates r = MIN … MAX; those with cost(r) ≤ budget are *affordable*.
2. If an affordable r covers the required pods R: choose the minimum J among them
   (usually the cheapest r ≥ R; more only if uncertainty makes the shortfall penalty large).
3. Otherwise (budget or MAX too small): choose the minimum J among affordable r, flag
   `capacity_shortfall`, and `BUDGET_LIMIT` if the budget is the binding constraint.
4. If not even MIN pods are affordable: keep MIN, status `INFEASIBLE`.

`OPTIMIZER_MODE=predictive` (method C in the experiments) skips the cost terms: it uses
R pods clipped to [MIN, MAX], ignoring the budget — used to measure the value of the
cost-aware part.

## Example (flash sale peak)

Forecast peak 64 req/s, margin 15 % → D = 73.6 req/s, μ·u = 14 → R = 6 pods.
Costs ₹5…₹40/h for 1…8 pods; budget ₹35 → affordable 1…7. Chosen: 6 pods (₹30/h), the
cheapest affordable count covering R. With a ₹15 budget: 3 pods, BUDGET LIMIT.
