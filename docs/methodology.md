# Methodology

## Problem statement

Given a web application on Kubernetes with a per-pod capacity μ (requests/s) and a price
c per pod-hour, choose the number of pods r(t) over time so that

* the offered load λ(t) is served within the latency objective (SLA: p95 ≤ 250 ms), and
* the cost Σ c·r(t)·Δt is as low as possible, never exceeding a budget B per hour,

while pods need a start-up time s (seconds) before they serve traffic.

A reactive policy sets r(t) from λ(t − δ) (δ = metric window + sync period) and only gets
capacity at t + s — it is late by roughly δ + s on every rise. A predictive policy sets
r(t) from a forecast λ̂(t + h) with h ≥ s, so capacity is ready when the load arrives.

## Pipeline (one iteration every Δ = 5 s)

1. **Measure** — `sum(rate(marketplace_http_requests_total{route=~"/api/.*"}[30s]))`
   (all shop requests including rejected ones = offered load), p95 latency, CPU, memory.
2. **Forecast** — LSTM over the last 12 samples (60 s) → next 6 samples (30 s).
3. **Plan** — peak P = max(forecast, current load); effective demand D = P(1 + m), m = 15 %;
   required pods R = ⌈D / (μ·u)⌉ with μ = 20 req/s and target utilisation u = 0.7.
4. **Optimise** — minimise J(r) = c·r + λ_SLA·E[(Λ − μ·u·r)⁺] + λ_chg·|r − r₀| over
   r ∈ [MIN, MAX], c·r ≤ B, r ≥ R (if feasible), Λ ~ N(P, σ²), σ = worst live horizon RMSE
   (or the model's test RMSE before live errors exist).
5. **Stabilise** — step limits, cooldowns, 60 s scale-down stabilisation, safe-mode rule.
6. **Act** — patch `deployments/scale`.

## Capacity calibration

One marketplace pod has a 250m CPU limit. Each request burns a fixed amount of CPU
(4–20 ms by route, weighted mean ≈ 8 ms plus framework overhead). In the sandbox
calibration (one process under a 250m CFS quota) throughput was 9.5/15.1/18.9/22.0 req/s
for offered 10/15/20/25 req/s with p95 90/182/180/496 ms, and errors appeared above
~30 req/s. CAPACITY_PER_POD = 20 req/s and a 70 % target (14 req/s per pod) leave headroom
for CFS throttling bursts. On a slower laptop the capacity can be lower; adjust
`CAPACITY_PER_POD` or `MARKETPLACE_WORK_SCALE`.

## Metrics used in the evaluation

SLA violation % (requests served above 250 ms or failed), average/p95/peak latency,
average and peak pods, number of scaling events, under-provisioned time, estimated cost,
peak budget utilisation, forecast MAE/RMSE. Definitions: `experiments/analysis.py`.

## Threats to validity

* Synthetic shoppers: the request mix and arrival process are modelled (Poisson
  arrivals, fixed mix), not recorded from a real store.
* Single-node Minikube: pods share the laptop's CPU; noisy neighbours (browser, IDE)
  affect latency.
* Simulated prices.
* The shipped model was trained on traffic shapes similar to the demo scenarios; its
  accuracy on unseen shapes is lower (the burst scenario shows this).
