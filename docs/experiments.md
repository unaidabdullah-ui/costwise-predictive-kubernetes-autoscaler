# Experiments

## Methods (identical traffic for all)

| | Method | How |
|---|---|---|
| A | Fixed | pods for the scenario's peak (+ margin), never changed |
| B | Reactive HPA | Kubernetes HPA algorithm (CPU 70 %, 15 s sync, 300 s scale-down stabilisation) — emulated in simulation, the real HPA (`kubernetes/experiments/hpa-baseline.yaml`) live |
| C | Predictive | LSTM + capacity planner, `OPTIMIZER_MODE=predictive` (no cost terms, no budget) |
| D | CostWise | LSTM + cost-aware optimiser under the budget (`OPTIMIZER_MODE=cost_aware`) |

## Scenarios

`constant`, `spike`, `ramp_up`, `periodic`, `bursty`, `flash_sale` (simulation);
`flash_sale`, `busy`, `burst` (live, through the traffic generator's plans).

## Running

```powershell
python -m experiments.run_experiment                          # simulation, all scenarios × methods
python -m experiments.run_experiment --seeds 1 2 3 --budget 25
python -m experiments.run_experiment --mode live --scenarios flash_sale --methods hpa cost_aware
python -m experiments.compare_methods --run experiments\results\<run-id>
```

Live mode needs a running demo (start-demo) and, for method B,
`minikube addons enable metrics-server`. It switches CostWise to observe mode while the
HPA or the fixed setting is measured, and restores it afterwards.

## Simulation model

`simulator/cluster_simulator.py` drives the **production** `AutoscalerController` (same
planner, optimiser, scaling policy, forecaster) against a simulated cluster with a pod
start-up delay (12 s), per-pod capacity (20 req/s), an M/M/1-style latency curve with a
queue limit, and a Prometheus-like 30 s rate window. Results are deterministic per seed.

## Shipped results (simulation only)

Two simulated runs produced with the code in this package (seed 42; see each
`run_metadata.json`, "kind": "simulation"):

* `experiments/results/shipped-simulation/` — default budget ₹35/h
* `experiments/results/shipped-simulation-budget20/` — tight budget ₹20/h (`--budget 20`)

Flash-sale scenario (from `aggregate.csv`; cost = estimated ₹ for the run; budget use =
peak cost rate ÷ budget):

| Method | SLA violations | p95 latency | avg pods | scaling events | est. cost | peak budget use |
|---|---|---|---|---|---|---|
| A Fixed (peak) | 0.00 % | 57 ms | 6.00 | 0 | 5.000 | 0.86 |
| B Reactive HPA | 28.54 % | 5,500 ms | 3.75 | 4 | 3.125 | 0.71 |
| C Predictive | 0.00 % | 71 ms | 3.33 | 12 | 2.778 | 0.86 |
| D CostWise | 0.00 % | 71 ms | 3.33 | 12 | 2.778 | 0.86 |
| D CostWise, budget ₹20 | 0.00 % | 161 ms | 2.63 | 8 | 2.194 | **1.00** |
| C Predictive, budget ₹20 | 0.00 % | 71 ms | 3.33 | 12 | 2.778 | 1.50 (over budget) |

Reading: on this scenario the reactive HPA is late (requests queue while pods start);
both predictive methods avoid violations with ~45 % less pod-time than fixed peak
provisioning. With the default budget, C and D choose the same pods (the budget does not
bind). With a tight budget, D stays exactly within it while C exceeds it by 50 %. On
scenarios with sudden, unannounced spikes (`spike`, `bursty`) no forecaster can see the
jump in advance; predictive methods still beat the HPA but have violations, and under a
tight budget CostWise trades violations for cost by design (bursty: 28.6 % vs 6.2 %).
Full tables: `comparison_table.md` in each folder. No live
(Minikube) results are shipped because Minikube was not available where this package was
built; run the live mode on your laptop to produce them.
