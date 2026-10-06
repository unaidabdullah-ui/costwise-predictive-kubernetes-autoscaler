# Convenience targets (Linux/macOS/WSL). Windows users: use the .ps1 scripts.
PY ?= python3

.PHONY: help install dev test lint typecheck train experiment report demo stop reset dashboard-dry-run grafana-json
help:            ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'
install:         ## install demo + experiment dependencies
	$(PY) -m pip install -r requirements.txt
dev:             ## install dev tools too
	$(PY) -m pip install -r requirements/dev.txt
test:            ## run the test suite
	$(PY) -m pytest
lint:            ## ruff
	ruff check .
typecheck:       ## mypy
	mypy autoscaler costwise common marketplace workload_generator ml/src/lite.py ml/src/numpy_lstm.py
train:           ## retrain the NumPy LSTM (bootstrap data + .costwise/history.csv if present)
	$(PY) -m ml.src.lite train
experiment:      ## simulated comparison: fixed vs HPA vs predictive vs cost-aware
	$(PY) -m experiments.run_experiment
report:          ## charts + table for the latest experiment
	$(PY) -m experiments.compare_methods
demo:            ## start everything on Minikube
	./start-demo.sh
stop:            ## stop the demo (cluster kept)
	./stop-demo.sh
reset:           ## reset the demo
	./reset-demo.sh
dashboard-dry-run: ## dashboard without a cluster (in-memory scaler, clearly labelled)
	$(PY) -m costwise --dry-run
grafana-json:    ## regenerate the Grafana dashboard JSON
	$(PY) grafana/generate_dashboard.py
