"""The demo forecaster: NumPy LSTM training, evaluation and inference.

Pipeline (``python -m ml.src.lite train``)::

    bootstrap data (labelled synthetic)  +  observed history (.costwise/history.csv, real)
          ↓ measured like Prometheus: rate[30s] sampled every 5 s
    features per point: rate, first difference, rolling mean, rolling std (window 6)
          ↓ chronological 70/15/15 split per series (no leakage)
    sliding windows: last 12 points (1 min) → change over the next 6 points (30 s)
          ↓ NumPy LSTM (32 units) + MLP head, Adam, early stopping on validation loss
    test metrics (MAE, RMSE, MAPE) against naive and Holt baselines
          ↓ saved atomically to ml/models/lstm_lite.npz (+ lstm_lite.json metadata)

**Training data, honestly labelled.** *Bootstrap* series are simulations of the
dashboard's traffic plans (normal, busy, flash sale, burst, recovery, ...) and
generic workload patterns, converted to what Prometheus would measure. They are
synthetic. *Observed* series are the request rates CostWise actually measured
(appended by the control loop to ``.costwise/history.csv``); RETRAIN MODEL adds
them. The model does not learn continuously: it changes only when retrained.

The network predicts the **change** from the current value (not the absolute
value), which keeps it stable at low traffic.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from common.patterns import trailing_mean
from ml.src.forecasters import Forecaster, HoltForecaster, ModelNotAvailableError
from ml.src.metrics import per_horizon_report, regression_report
from ml.src.numpy_lstm import Adam, NumpyLSTM

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = PROJECT_ROOT / "ml" / "models" / "lstm_lite.npz"
DEFAULT_HISTORY_PATH = PROJECT_ROOT / ".costwise" / "history.csv"

RATE_SCALE = 50.0  # req/s → network units
DIFF_SCALE = 10.0


@dataclass
class LiteConfig:
    """Architecture and training settings of the demo LSTM (small on purpose)."""

    sequence_length: int = 12  # 12 × 5 s = 1 minute of history
    horizon: int = 6  # 6 × 5 s = 30 seconds ahead
    step_seconds: float = 5.0
    rolling_window: int = 6
    hidden_size: int = 32
    epochs: int = 14
    batch_size: int = 128
    learning_rate: float = 3e-3
    lr_decay: float = 0.9
    patience: int = 3
    seed: int = 42
    demo_runs: int = 90
    generic_series: int = 30
    observed_weight: int = 3  # observed windows are repeated in the training set

    def validate(self) -> None:
        if self.sequence_length < 2 or self.horizon < 1:
            raise ValueError("sequence_length must be >= 2 and horizon >= 1")
        if self.hidden_size < 1 or self.epochs < 1 or self.batch_size < 1:
            raise ValueError("hidden_size, epochs and batch_size must be >= 1")
        if self.step_seconds <= 0 or self.learning_rate <= 0:
            raise ValueError("step_seconds and learning_rate must be > 0")


# ----------------------------------------------------------------------------- features
def make_features(rate: np.ndarray, window: int = 6) -> np.ndarray:
    """``(T, 4)`` features: rate, first difference, rolling mean, rolling std (all scaled)."""
    r = np.asarray(rate, dtype=float)
    diff = np.diff(r, prepend=r[:1])
    mean = trailing_mean(r, window)
    sq = trailing_mean(r * r, window)
    std = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    return np.stack([r / RATE_SCALE, diff / DIFF_SCALE, mean / RATE_SCALE, std / DIFF_SCALE], axis=1)


def make_windows(rate: np.ndarray, cfg: LiteConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Windows ``X (N, L, 4)``, scaled change targets ``Y (N, H)`` and last values ``(N,)``."""
    r = np.asarray(rate, dtype=float)
    L, H = cfg.sequence_length, cfg.horizon
    n = r.size - L - H + 1
    if n <= 0:
        return np.empty((0, L, 4)), np.empty((0, H)), np.empty(0)
    feats = make_features(r, cfg.rolling_window)
    xi = np.arange(L)[None, :] + np.arange(n)[:, None]
    last = r[L - 1 : L - 1 + n]
    yi = np.arange(1, H + 1)[None, :] + (np.arange(n) + L - 1)[:, None]
    return feats[xi], (r[yi] - last[:, None]) / RATE_SCALE, last


# ------------------------------------------------------------------------- bootstrap data
def measure_like_prometheus(per_second_rate: np.ndarray, rng: np.random.Generator,
                            step: float = 5.0, window_s: int = 30) -> np.ndarray:
    """Poisson request counts → ``rate()[30s]`` sampled every ``step`` seconds."""
    counts = rng.poisson(np.maximum(per_second_rate, 0.0))
    smoothed = trailing_mean(counts.astype(float), window_s)
    return smoothed[window_s - 1 :: int(step)]


def demo_run(rng: np.random.Generator, rate_per_shopper: float = 0.04) -> np.ndarray:
    """One simulated dashboard session (random sequence of plans), measured every 5 s."""
    from workload_generator.plans import Plan

    choices = ["flash_sale", "busy", "burst", "users", "recovery", "stopped", "normal", "flash_sale"]
    durations = {"flash_sale": (200, 330), "busy": (150, 300), "burst": (240, 270), "users": (40, 120),
                 "recovery": (60, 150), "stopped": (40, 120), "normal": (60, 240)}
    shoppers: list[float] = []
    current = 0.0
    sequence = ["normal"] + list(rng.choice(choices, size=int(rng.integers(3, 6))))
    for name in sequence:
        target = current + 300.0 * int(rng.integers(1, 4)) if name == "users" else None
        plan = Plan(name, current, target=target, phase=float(rng.uniform(0, 6.28)))
        for t in range(int(rng.integers(*durations[name]))):
            current = plan.shoppers(float(t))
            shoppers.append(current)
    s = np.asarray(shoppers)
    jitter = np.exp(np.cumsum(rng.normal(0, 0.01, size=s.size)) * 0.3)  # slow drift around the plan
    scale = rng.uniform(0.6, 1.4)  # other WORKLOAD_RATE / catalogue mixes
    return measure_like_prometheus(s * jitter * rate_per_shopper * scale, rng)


def generic_run(rng: np.random.Generator, length_steps: int = 260) -> np.ndarray:
    """A generic pattern (ramps, waves, spikes, bursts) in the demo's rate range, measured every 5 s."""
    from ml.src.data_generator import PATTERNS, generate_series

    pattern = str(rng.choice(PATTERNS))
    base = generate_series(pattern, length_steps * 5, 1.0, rng) * rng.uniform(0.25, 0.6)
    return measure_like_prometheus(base, rng)


def bootstrap_series(cfg: LiteConfig) -> list[np.ndarray]:
    """The labelled synthetic bootstrap dataset (deterministic for ``cfg.seed``)."""
    rng = np.random.default_rng(cfg.seed)
    runs = [demo_run(rng) for _ in range(cfg.demo_runs)]
    runs += [generic_run(rng) for _ in range(cfg.generic_series)]
    return [r for r in runs if r.size >= 3 * (cfg.sequence_length + cfg.horizon)]


def load_observed(path: Path, step: float = 5.0, min_points: int = 54) -> list[np.ndarray]:
    """Contiguous measured segments from the control loop's history CSV (real data)."""
    if not path.is_file():
        return []
    times, rates = [], []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                ts_raw = row["timestamp"]
                ts = float(ts_raw) if ts_raw.replace(".", "", 1).isdigit() else \
                    datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                rate = float(row["request_rate"])
            except (KeyError, ValueError, TypeError):
                continue
            if np.isfinite(rate) and rate >= 0:
                times.append(ts)
                rates.append(rate)
    if not times:
        return []
    order = np.argsort(times)
    t, r = np.asarray(times)[order], np.asarray(rates)[order]
    breaks = np.where(np.diff(t) > 3 * step)[0] + 1
    segments = [seg for seg in np.split(r, breaks) if seg.size >= min_points]
    return segments


# ------------------------------------------------------------------------------ training
@dataclass
class _Split:
    x: np.ndarray
    y: np.ndarray
    last: np.ndarray


def _split_windows(series: list[np.ndarray], cfg: LiteConfig, repeat: int = 1
                   ) -> tuple[_Split, _Split, _Split]:
    parts: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {"train": [], "val": [], "test": []}
    for s in series:
        n = s.size
        a, b = int(n * 0.70), int(n * 0.85)
        for name, seg in (("train", s[:a]), ("val", s[a:b]), ("test", s[b:])):
            x, y, last = make_windows(seg, cfg)
            if len(x):
                parts[name].extend([(x, y, last)] * (repeat if name == "train" else 1))

    def cat(items: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> _Split:
        if not items:
            return _Split(np.empty((0, cfg.sequence_length, 4)), np.empty((0, cfg.horizon)), np.empty(0))
        return _Split(*(np.concatenate([it[k] for it in items]) for k in range(3)))

    return cat(parts["train"]), cat(parts["val"]), cat(parts["test"])


def _merge(a: _Split, b: _Split) -> _Split:
    return _Split(np.concatenate([a.x, b.x]), np.concatenate([a.y, b.y]), np.concatenate([a.last, b.last]))


def _batched_loss(model: NumpyLSTM, data: _Split, batch: int = 2048) -> float:
    if len(data.x) == 0:
        return float("nan")
    total = 0.0
    for i in range(0, len(data.x), batch):
        pred = model.predict(data.x[i : i + batch])
        total += float(np.sum((pred - data.y[i : i + batch]) ** 2))
    return total / data.y.size


def _absolute(data: _Split, scaled_change: np.ndarray) -> np.ndarray:
    return np.maximum(data.last[:, None] + scaled_change * RATE_SCALE, 0.0)


def evaluate_split(model: NumpyLSTM, data: _Split, cfg: LiteConfig) -> dict[str, Any]:
    """Test metrics in req/s for the LSTM and the naive / Holt baselines."""
    actual = _absolute(data, data.y)
    lstm = _absolute(data, model.predict(data.x))
    naive = np.repeat(data.last[:, None], cfg.horizon, axis=1)
    raw_hist = data.x[:, :, 0] * RATE_SCALE
    holt = HoltForecaster(cfg.horizon, sequence_length=cfg.sequence_length)
    holt_pred = np.stack([holt.predict(np.maximum(h, 0.0)) for h in raw_hist]) if len(raw_hist) else naive
    out: dict[str, Any] = {}
    for name, pred in (("lstm", lstm), ("naive", naive), ("holt", holt_pred)):
        out[name] = {"overall": regression_report(actual, pred), "per_horizon": per_horizon_report(actual, pred)}
    out["uncertainty_rmse"] = max(step["rmse"] for step in out["lstm"]["per_horizon"])
    naive_rmse = out["naive"]["overall"]["rmse"]
    out["rmse_improvement_vs_naive_pct"] = (
        100.0 * (naive_rmse - out["lstm"]["overall"]["rmse"]) / naive_rmse if naive_rmse > 0 else None
    )
    return out


def train(cfg: LiteConfig, out_path: Path = DEFAULT_MODEL_PATH, history_path: Path | None = None,
          progress: bool = False) -> dict[str, Any]:
    """Train on bootstrap (+ observed) data, evaluate, save atomically; returns the metadata."""
    cfg.validate()
    started = time.perf_counter()
    rng = np.random.default_rng(cfg.seed)
    boot = bootstrap_series(cfg)
    observed = load_observed(history_path, cfg.step_seconds) if history_path else []
    b_train, b_val, b_test = _split_windows(boot, cfg)
    if observed:
        o_train, o_val, o_test = _split_windows(observed, cfg, repeat=cfg.observed_weight)
        train_set, val_set = _merge(b_train, o_train), _merge(b_val, o_val)
        test_set = _merge(b_test, o_test)
    else:
        o_test = None
        train_set, val_set, test_set = b_train, b_val, b_test
    if not (len(train_set.x) and len(val_set.x) and len(test_set.x)):
        raise ValueError("not enough data to train")

    model = NumpyLSTM(4, cfg.hidden_size, cfg.horizon, seed=cfg.seed)
    optimiser = Adam(model.params, lr=cfg.learning_rate, clip_norm=1.0)
    best, best_val, bad, history = model.copy_params(), float("inf"), 0, []
    for epoch in range(1, cfg.epochs + 1):
        order = rng.permutation(len(train_set.x))
        running = 0.0
        for i in range(0, len(order), cfg.batch_size):
            idx = order[i : i + cfg.batch_size]
            loss, grads = model.loss_and_grads(train_set.x[idx], train_set.y[idx])
            optimiser.step(model.params, grads)
            running += loss * len(idx)
        train_loss = running / len(order)
        val_loss = _batched_loss(model, val_set)
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        if progress:
            print(f"  epoch {epoch:2d}  train {train_loss:.5f}  validation {val_loss:.5f}", flush=True)
        if val_loss < best_val - 1e-7:
            best_val, best, bad = val_loss, model.copy_params(), 0
        else:
            bad += 1
            if bad >= cfg.patience:
                break
        optimiser.lr *= cfg.lr_decay
    model.set_params(best)

    metrics = evaluate_split(model, test_set, cfg)
    observed_metrics = evaluate_split(model, o_test, cfg) if o_test is not None and len(o_test.x) else None
    metadata: dict[str, Any] = {
        "model": "LSTM (NumPy)",
        "format": "costwise-lstm-lite/1",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_seconds": round(time.perf_counter() - started, 1),
        "parameters": model.parameter_count(),
        "config": asdict(cfg),
        "scaling": {"rate_scale": RATE_SCALE, "diff_scale": DIFF_SCALE, "target": "change from last value"},
        "data": {
            "bootstrap_series": len(boot),
            "bootstrap_points": int(sum(s.size for s in boot)),
            "bootstrap_label": "synthetic: simulated demo traffic plans + generic patterns, measured like Prometheus",
            "observed_series": len(observed),
            "observed_points": int(sum(s.size for s in observed)),
            "observed_label": "real: request rate measured by Prometheus during your demos",
            "train_windows": int(len(train_set.x)),
            "validation_windows": int(len(val_set.x)),
            "test_windows": int(len(test_set.x)),
        },
        "history": history,
        "best_validation_loss": best_val,
        "test_metrics": metrics,
        "observed_test_metrics": observed_metrics,
    }
    save_model(model, metadata, out_path)
    return metadata


def save_model(model: NumpyLSTM, metadata: dict[str, Any], out_path: Path) -> None:
    """Write ``.npz`` + ``.json`` atomically (a failed write never replaces a good model)."""
    from common.jsonutil import to_jsonable

    clean = to_jsonable(metadata)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    model.save(tmp, clean)
    NumpyLSTM.load(tmp)  # verify it can be read back before replacing anything
    os.replace(tmp, out_path)
    json_tmp = out_path.with_suffix(".json.tmp")
    json_tmp.write_text(json.dumps(clean, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(json_tmp, out_path.with_suffix(".json"))


# ----------------------------------------------------------------------------- inference
class LiteLSTMForecaster(Forecaster):
    """The trained NumPy LSTM behind the :class:`Forecaster` interface."""

    name = "lstm_lite"
    label = "LSTM"

    def __init__(self, model: NumpyLSTM, cfg: LiteConfig, metadata: dict[str, Any], path: Path) -> None:
        super().__init__(cfg.horizon, cfg.sequence_length)
        self.model, self.cfg, self.metadata, self.path = model, cfg, metadata, path

    @classmethod
    def load(cls, path: Path | None = None) -> LiteLSTMForecaster:
        path = Path(path) if path else Path(os.environ.get("LITE_MODEL_PATH") or DEFAULT_MODEL_PATH)
        if not path.is_file():
            raise ModelNotAvailableError(
                f"LSTM model file not found ({path.name}). Train it: python -m ml.src.lite train"
            )
        try:
            model, metadata = NumpyLSTM.load(path)
        except Exception as exc:  # corrupt / incompatible file
            raise ModelNotAvailableError(f"LSTM model file could not be read: {exc}") from exc
        known = {k: v for k, v in metadata.get("config", {}).items() if k in LiteConfig.__dataclass_fields__}
        cfg = LiteConfig(**known)
        if model.n_features != 4 or model.n_outputs != cfg.horizon:
            raise ModelNotAvailableError("model file does not match its configuration")
        return cls(model, cfg, metadata, path)

    @property
    def uncertainty(self) -> float | None:
        try:
            return float(self.metadata["test_metrics"]["uncertainty_rmse"])
        except (KeyError, TypeError, ValueError):
            return None

    def _predict(self, history: np.ndarray) -> np.ndarray:
        lookback = self.cfg.sequence_length + self.cfg.rolling_window - 1
        recent = history[-lookback:]
        feats = make_features(recent, self.cfg.rolling_window)[-self.cfg.sequence_length :]
        change = self.model.predict(feats[None, :, :])[0]
        return np.maximum(recent[-1] + change * RATE_SCALE, 0.0)

    def info(self) -> dict[str, Any]:
        base = super().info()
        tm = self.metadata.get("test_metrics", {})
        lstm = tm.get("lstm", {}).get("overall", {})
        naive = tm.get("naive", {}).get("overall", {})
        base.update({
            "architecture": f"LSTM({self.cfg.hidden_size}) → Linear → ReLU → Linear({self.cfg.horizon})",
            "parameters": self.metadata.get("parameters"),
            "step_seconds": self.cfg.step_seconds,
            "horizon_seconds": self.cfg.horizon * self.cfg.step_seconds,
            "history_seconds": self.cfg.sequence_length * self.cfg.step_seconds,
            "trained_at": self.metadata.get("trained_at"),
            "training_seconds": self.metadata.get("training_seconds"),
            "test_mae": lstm.get("mae"),
            "test_rmse": lstm.get("rmse"),
            "test_mape": lstm.get("mape"),
            "naive_rmse": naive.get("rmse"),
            "rmse_improvement_vs_naive_pct": tm.get("rmse_improvement_vs_naive_pct"),
            "data": self.metadata.get("data", {}),
            "model_file": self.path.name,
        })
        return base


# ----------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train / inspect the CostWise NumPy LSTM forecaster.")
    sub = parser.add_subparsers(dest="command", required=True)
    tr = sub.add_parser("train", help="train on bootstrap (+ observed) data and save the model")
    tr.add_argument("--history", type=Path, default=None,
                    help=f"observed history CSV (default {DEFAULT_HISTORY_PATH.relative_to(PROJECT_ROOT)} if present)")
    tr.add_argument("--no-history", action="store_true", help="bootstrap data only")
    tr.add_argument("--out", type=Path, default=DEFAULT_MODEL_PATH)
    tr.add_argument("--epochs", type=int)
    tr.add_argument("--seed", type=int)
    tr.add_argument("--quick", action="store_true", help="small dataset, for tests")
    tr.add_argument("--json", action="store_true", help="print the metadata as JSON (machine-readable)")
    info = sub.add_parser("info", help="show the saved model's metadata")
    info.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    args = parser.parse_args(argv)

    if args.command == "info":
        forecaster = LiteLSTMForecaster.load(args.model)
        print(json.dumps(forecaster.info(), indent=2))
        return 0

    cfg = LiteConfig()
    if args.epochs:
        cfg.epochs = args.epochs
    if args.seed is not None:
        cfg.seed = args.seed
    if args.quick:
        cfg.demo_runs, cfg.generic_series, cfg.epochs = 12, 4, 3
    history = None if args.no_history else (args.history or DEFAULT_HISTORY_PATH)
    if not args.json:
        print("Training the CostWise LSTM (NumPy, CPU) ...", flush=True)
    meta = train(cfg, args.out, history, progress=not args.json)
    if args.json:
        from common.jsonutil import dumps

        print(dumps({k: meta[k] for k in ("trained_at", "training_seconds", "data", "test_metrics")}))
        return 0
    tm = meta["test_metrics"]
    print(f"\nSaved {args.out} ({meta['parameters']} parameters, {meta['training_seconds']} s)")
    print(f"Data: {meta['data']['bootstrap_points']} bootstrap points (synthetic), "
          f"{meta['data']['observed_points']} observed points (real)")
    for name in ("lstm", "naive", "holt"):
        o = tm[name]["overall"]
        print(f"  {name:<6} MAE {o['mae']:6.2f}  RMSE {o['rmse']:6.2f}  MAPE {o['mape']:6.1f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
