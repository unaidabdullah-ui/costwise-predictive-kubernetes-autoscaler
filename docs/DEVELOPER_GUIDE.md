# Developer guide

## Setup

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
pytest                                                # ~1-2 minutes
ruff check . && mypy autoscaler costwise common marketplace workload_generator ml/src/lite.py ml/src/numpy_lstm.py
```

## Run pieces locally without Kubernetes

```bash
MARKETPLACE_PORT=8080 python -m marketplace                          # store on :8080
TARGET_URL=http://127.0.0.1:8080 CONTROL_TOKEN=dev python -m workload_generator   # :8089
prometheus --config.file=prometheus/prometheus-local.yml             # :9090
CONTROL_TOKEN=dev python -m costwise --dry-run                       # dashboard :8000 (DRY RUN scaler)
```

## Code map

* `autoscaler/controller.py` — one iteration = `_iterate()`; everything else is injected
  (`build_controller`), which is how the tests and the simulator reuse it.
* `costwise/runtime.py` — threads, demo actions, dashboard JSON (`dashboard()`).
* `costwise/server.py` — routes and the security middleware.
* `costwise/static/` — `app.js` (pages, polling), `chart.js` (SVG chart), `app.css`.
  No build step, no external libraries; CSP forbids inline scripts/styles.
* `ml/src/lite.py` — features, bootstrap data, training, forecaster; `numpy_lstm.py` — the network.
* `workload_generator/plans.py` — scenario shapes (shared with the bootstrap data).

## Adding a traffic scenario

1. Add a `PlanInfo` and a branch in `Plan.shoppers()` (`workload_generator/plans.py`).
2. Add it to `DEMO_SCENARIOS` in `costwise/runtime.py` and, if wanted, a button in `app.js`.
3. Add it to `choices`/`durations` in `demo_run()` (`ml/src/lite.py`) and retrain
   (`python -m ml.src.lite train --no-history`) so the bootstrap data includes the new shape.

## Changing the application's cost per request

`ROUTE_COST_MS` in `marketplace/app.py` or `MARKETPLACE_WORK_SCALE` (env). Re-calibrate
`CAPACITY_PER_POD` (see docs/methodology.md) and rebuild the image (`start-demo -Rebuild`).

## Conventions

Python 3.10+, type hints, ruff (line length 120), no secrets in code or logs (the logging
formatter redacts token/password keys), every number on the dashboard must come from a
measurement or a model output — never a constant.
