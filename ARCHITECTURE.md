# CostWise architecture

## Components

| Component | Where it runs | Code | Role |
|---|---|---|---|
| COSTWISE MARKETPLACE | Minikube, Deployment `marketplace` (1–8 pods, 250m CPU limit) | `marketplace/` | The application being autoscaled: storefront + JSON shop API, `/metrics` |
| Traffic generator | Minikube, Deployment `traffic-generator` (1 pod) | `workload_generator/` | Simulated shoppers → real HTTP requests; scenario control API (token) |
| Prometheus | Minikube, Deployment `prometheus` | `prometheus/`, `kubernetes/monitoring/` | Scrapes pods every 5 s (pod discovery limited to the namespace), alert/recording rules |
| CostWise control plane | Laptop, `python -m costwise` (127.0.0.1:8000) | `costwise/`, `autoscaler/`, `ml/src/` | Controller loop + dashboard + demo actions |
| Grafana (optional) | Minikube | `grafana/`, `kubernetes/optional/grafana.yaml` | Read-only Prometheus dashboard |

## Data and control flow (every 5 s)

```
Prometheus ──(PromQL over HTTP)──► MetricsSource ──► WorkloadSnapshot (req/s now + 60 s history, p95, CPU, memory)
                                                         │
                         ForecastTracker ◄───────────────┤ (matures earlier forecasts → live MAE/RMSE, trust score)
                                                         ▼
                                  LocalPredictor (NumPy LSTM) ──► forecast[6] (next 30 s)   ── invalid/untrusted → SAFE MODE
                                                         ▼
                                  CapacityPlanner ──► peak, effective demand (+15 %), σ, required pods
                                                         ▼
                                  CostAwareOptimizer ──► J(r) for r = MIN..MAX under the budget → desired pods
                                                         ▼
                                  ScalingEngine ──► cooldowns, stabilisation, step limits, safe-mode rule → target
                                                         ▼
                                  KubernetesScaler ──► PATCH deployments/marketplace/scale  (only if active mode)
                                                         ▼
                      DecisionRecord ──► state (dashboard) · events (timeline) · metrics · history.csv (retraining)
```

The `Runtime` (`costwise/runtime.py`) runs this loop in one thread and a 2-second
*poller* in another (Deployment status, pod list, traffic-generator status, error ratio,
component health). The Starlette server (`costwise/server.py`) only reads the runtime's
snapshot and calls a fixed set of demo actions.

## Why the control plane runs on the laptop

* RAM: a 4 GB laptop cannot comfortably add another Python pod with NumPy to Minikube.
* Simplicity for the demo: logs and the dashboard are local; no ingress needed.
* It still uses Kubernetes the "right" way: a ServiceAccount, a namespace-scoped Role,
  and a short-lived token (`kubectl create token --duration=12h`).
* An in-cluster variant is provided: `docker/control-plane/Dockerfile` +
  `kubernetes/optional/control-plane.yaml`.

## Connectivity

`start-demo` runs three supervised `kubectl port-forward`s (each in a restart loop):
`svc/prometheus 9090`, `svc/traffic-generator 8089`, `svc/marketplace 8080→80`.
A port-forward to a Service is bound to one pod; when scale-down removes that pod the
forward exits and the supervisor starts a new one within a second. CostWise itself does
not depend on the 8080 forward (only the "store link" health indicator does) — the
shoppers reach the store through the in-cluster Service, which spreads requests over all
ready pods (`Connection: close` per request so new pods get traffic immediately).

## Failure handling

| Failure | Behaviour |
|---|---|
| Prometheus unreachable / no data | HOLD current pods; pill red; event "metrics unavailable" |
| Kubernetes unreachable / 401 / 403 / 404 | HOLD; message with the fix (e.g. token expired → re-run start-demo) |
| Model file missing / unreadable | Safe mode (plan on measured load, no scale-down); RETRAIN MODEL can create it |
| Forecast NaN / negative / wrong length | Safe mode for that iteration |
| Live trust score < 40 % | Safe mode until the model earns trust back |
| Traffic generator down | Demo buttons return an error with a hint; autoscaling continues |
| Budget too small for MIN pods | Keep MIN pods, report *INFEASIBLE* |
| Any unexpected exception | Logged, counted, HOLD; the loop continues |

## Security model

See README §13 and `kubernetes/rbac.yaml`. Trust boundaries: browser ↔ dashboard
(loopback, header + origin + host checks, CSP, fixed actions only); dashboard ↔
Kubernetes (least-privilege token); dashboard ↔ traffic generator (random shared token
from a Secret); Prometheus (read-only pod discovery Role in the namespace).

## Design decisions

* **NumPy LSTM instead of PyTorch at runtime** — 6k parameters, millisecond inference,
  no 2 GB dependency on a 4 GB laptop; gradient-checked. PyTorch pipeline kept as an option.
* **Dependency-free Prometheus exposition** (`common/promtext.py`) — small images.
* **Exact enumeration** for the optimiser — at most 8–100 candidates, provably optimal,
  explainable (the Technical page lists every candidate).
* **Forecast the change, not the level** — the LSTM predicts the change from the last
  measured value (scaled), which generalises across load levels and avoids lag.
* **Trust score relative to a naive forecast** — the model is trusted while it is not
  much worse than "traffic stays the same" (with a one-pod tolerance), which does not
  penalise it on ramps where every forecaster has larger absolute errors.
