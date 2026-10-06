# The forecasting model

## Task

Multi-step forecasting of the marketplace request rate: input the last 12 samples
(5 s apart, 60 s), output the next 6 samples (30 s). 30 s ≥ pod start-up time (≈10–15 s
on Minikube) + one decision interval, so a forecast-driven scale-up can be ready in time.

## Features and target

Per time step: rate (÷50), first difference (÷10), 6-step trailing mean (÷50) and
6-step trailing standard deviation (÷10). Target: the *change* of each future step relative to
the last observed value (÷50). Predicting changes generalises across load levels.
Forecasts are clipped at 0.

## Network

`LSTM(4 → 32) → Linear(32 → 32) → ReLU → Linear(32 → 6)`, 5,990 parameters, MSE loss,
Adam (lr 3e-3, decay 0.9/epoch, gradient clipping 1.0), early stopping (patience 3),
≤ 14 epochs, batch 128. Implemented in NumPy with hand-written backpropagation through
time (`ml/src/numpy_lstm.py`); `tests/test_ml.py` checks the gradients against finite
differences (max relative error < 1e-4).

## Data

| Source | Label in metadata | Used |
|---|---|---|
| 90 simulated demo runs (normal, busy, flash sale, burst, recovery, users, random order) + 30 generic series (daily, spikes, ramps), measured like Prometheus (Poisson counts, 30 s rate window, scrape jitter) | `synthetic` (bootstrap) | always |
| Request rate measured by Prometheus during your demos (`.costwise/history.csv`) | `real` (observed) | only after RETRAIN MODEL; contiguous segments ≥ 54 points (4.5 min), weighted ×3 |

Splits are chronological within each series (first 70 % train, next 15 % validation, last 15 % test). The shipped model
(`ml/models/lstm_lite.npz` + `.json`) used bootstrap data only — 22,615 points,
13,734/1,373/1,441 train/validation/test windows.

## Results (held-out test windows, shipped model)

| | MAE | RMSE | MAPE |
|---|---|---|---|
| LSTM | 2.29 | 4.12 | 13.8 % |
| Naive (last value) | 4.79 | 8.44 | 26.5 % |
| Holt (double exponential smoothing) | — | 10.32 | — |

Per horizon step RMSE: 0.94, 1.82, 2.93, 4.15, 5.36, 6.57 req/s (5…30 s ahead).
All numbers are produced by `python -m ml.src.lite train` and stored in the model's JSON.

## Live accuracy and trust

`autoscaler/forecast_tracker.py` keeps every forecast until its target time, then records
the error of the model and of the naive forecast (the load when the forecast was made).
Trust score = 1 − MAE_model / (MAE_naive + one pod of traffic) over the last 24 matured
30-s forecasts. Below `MIN_FORECAST_CONFIDENCE` (0.4) the controller enters safe mode.

## Retraining

`RETRAIN MODEL` (dashboard) or `python -m ml.src.lite train` — separate process, atomic
write (a failed training never replaces a working model), hot reload, result in the
timeline. `python -m ml.src.lite train --no-history` (or `reset-demo.ps1 -RestoreModel`)
recreates the bootstrap-only model. Training is deterministic (seed 42).

## Optional PyTorch pipeline

`ml/src/train.py`, `model.py`, `evaluate.py`, `predict.py` implement the same task with
PyTorch (and use a CUDA GPU such as an RTX 3070 if present). Install
`requirements/torch.txt`; select it with `FORECASTER=lstm` and `MODEL_PATH=<dir>`. The demo
does not need it.
