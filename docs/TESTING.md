# Testing report

Environment where this package was built and tested: Linux (x86-64, 2 vCPUs, 8 GB RAM),
Python 3.13, Starlette 1.6, Uvicorn 0.53, NumPy, requests, pandas, matplotlib,
Prometheus 2.53.2 + promtool, Chromium (Playwright). **Not available there: Docker,
Minikube, kubectl, a Kubernetes API server, the `kubernetes` Python package, PowerShell,
Grafana, PyTorch, FastAPI.**

## 1. Executed

| What | Result |
|---|---|
| `pytest` — 89 tests: config validation, capacity planner, optimiser (incl. budget limit / infeasible), scaling policy (cooldown, stabilisation, step limits, safe mode, out-of-range), forecast tracker / trust score, Kubernetes client with a fake API (scale sub-resource body, retries, 401/403/404/connection errors), Prometheus client with a fake HTTP session, controller loop (scale ahead, budget never exceeded, observe mode, Prometheus/Kubernetes outage, safe mode, reset, validated overrides), NumPy LSTM gradient check + save/load, quick training, shipped model loading, marketplace over a real uvicorn server (all endpoints, cart/checkout, metrics, 503 load shedding), traffic generator (plans, Poisson, control-token checks, real traffic to a real store), control plane over real servers (demo flow, settings validation, security headers/CSRF/Host checks, Prometheus outage, RETRAIN MODEL really retrains and hot-reloads), insights and explanations, manifest/RBAC/PSA/script checks, the simulator | **89 passed** |
| `ruff check .` | passed |
| `mypy` on autoscaler, costwise, common, marketplace, workload_generator, ml/src/lite.py, ml/src/numpy_lstm.py, experiments, simulator | passed |
| `promtool check config` on `prometheus/prometheus.yml` (rule path adjusted to a local copy), `prometheus-local.yml`, `docker/compose-prometheus.yml`; `promtool check rules` | passed (7 rules) |
| All YAML manifests parse; Grafana dashboard JSON generated and embedded | passed |
| Bash scripts `bash -n` (syntax) | passed |
| Marketplace + traffic generator started from exactly the files the Dockerfile copies, with NumPy/requests/kubernetes imports blocked | both served `/health`, store served `/` |
| Python 3.11 byte-compilation of all sources | passed |
| Simulation experiments (`shipped-simulation`, `shipped-simulation-budget20`) | produced the shipped results |
| **End-to-end faculty demo in the sandbox** (below) | completed, 0 browser console errors |

### End-to-end faculty demo (sandbox)

`tools/sandbox/e2e.py`. Real: marketplace processes (each limited to **250m CPU with a
Linux CFS quota**, as a Kubernetes CPU limit is), the traffic generator service, Prometheus
2.53.2 scraping every 5 s, the CostWise control plane and dashboard, Chromium clicking the
buttons. **Emulated: Kubernetes** (pods = processes started/stopped by a stand-in scaler
with a 5 s readiness delay; Service = round-robin TCP proxy).

Sequence: START DEMO → 100 s → FLASH SALE → 200 s (presentation mode at the peak) →
STOP TRAFFIC → 110 s → Technical details and Architecture pages → RETRAIN MODEL →
RESET DEMO → storefront browse/search/add to cart/checkout. Measured (2-second samples
of `/api/dashboard`, 230 samples, no failed request to the dashboard):

* normal traffic: 160–190 shoppers, 6–7 req/s, 1–2 pods (one early extra pod while the
  start-up ramp was in the model's 60 s window);
* flash sale: up to ~1,670 shoppers, ~66 req/s; pods 2 → 4 → 5 → 6 while the load was
  10 → 20 → 31 → 48 req/s (forecast peaks 23 → 40 → 60 → 63); maximum p95 latency
  122 ms, error ratio 0 %, shopper success 100 %; cost ₹30/h of ₹35/h;
* "Reactive vs predictive" card at the peak: 3 of 5 scale-ups ahead of the load, average
  head start 18 s, measured pod start-up 10 s;
* after STOP TRAFFIC: pods 6 → 5 → 3 → 2 → 1 within ~2.5 minutes, cost back to ₹5/h;
* RETRAIN MODEL: retrained in ~9 s on 22,615 bootstrap + 84 measured samples, hot-reloaded
  (a copy of the model was used so the shipped model stays bootstrap-only);
* RESET DEMO: traffic stopped, timeline/charts cleared, 1 pod.

Earlier sandbox runs found and fixed: a trust score that put the controller in safe mode
at very low traffic (replaced by a skill score relative to the naive forecast), a
lead-time calculation that missed crossings after the next scale-up, 1↔2 pod flapping
during the start-up ramp (scale-down stabilisation raised from 30 s to 60 s).

Screenshots: `docs/screenshots/` (all from the sandbox; pod names `marketplace-sbx-…`).
Capacity calibration of one 250m pod in the same sandbox: see docs/methodology.md.

## 2. Not executed in this environment because Kubernetes/Docker was unavailable

* `start-demo.ps1`, `stop-demo.ps1`, `reset-demo.ps1` — **not run** (no PowerShell, no
  Minikube). Their structure is checked by `tests/test_manifests.py`; the `.sh`
  equivalents were only syntax-checked.
* Minikube start, `minikube image build`, `kubectl apply` of the manifests, Pod Security
  Admission, RBAC enforcement (`kubectl auth can-i` commands in docs/kubernetes.md),
  ServiceAccount token creation, port-forward supervision.
* The official `kubernetes` Python client against a real API server
  (`KubernetesScaler`, `KubernetesClusterView`) — tested only with fakes.
* Docker image builds (`docker/marketplace/Dockerfile`, `docker/control-plane/Dockerfile`)
  and `docker compose up`.
* Prometheus Kubernetes pod discovery (`kubernetes_sd_configs`) — config validated by
  promtool, not run against a cluster.
* Grafana, the in-cluster control plane, the HPA baseline and
  `experiments.run_experiment --mode live`.
* The optional PyTorch pipeline (`ml/src/train.py` etc.) — PyTorch not installed.
* Windows 11 itself (paths, PowerShell 5.1 behaviour, Docker Desktop).

## 3. How to verify on your laptop

1. `.\start-demo.ps1` — all eight steps should end with OK.
2. RBAC: the four `kubectl auth can-i` commands in docs/kubernetes.md (yes / no / no / no).
3. Dashboard pills all OK; START DEMO → FLASH SALE: pods should rise before the blue line peaks.
4. `kubectl -n costwise get pods -w` in a second window shows the same pods as the dashboard.
5. `python -m pytest` in `.venv` (after `pip install -r requirements\dev.txt`).
6. `python -m experiments.run_experiment --mode live --scenarios flash_sale` for your own
   live comparison (~45 minutes for four methods).
