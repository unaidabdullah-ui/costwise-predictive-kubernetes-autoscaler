"""Train the PyTorch LSTM workload forecaster (optional offline pipeline).

    python -m ml.src.train                  # defaults (CPU, or CUDA if available)
    python -m ml.src.train --epochs 40 --hidden-size 64
    python -m ml.src.train --quick          # small/fast smoke run

The live demo uses ``python -m ml.src.lite train`` (NumPy, no PyTorch); this
pipeline is the larger reference implementation and needs ``requirements/full.txt``.

Outputs (``TORCH_MODEL_DIR``, default ``ml/models``): ``lstm_forecaster.pt``,
``feature_scaler.joblib``, ``target_scaler.joblib``, ``model_config.json``,
``training_metadata.json``; plots in ``ml/artifacts``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import logging
import platform
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError as exc:  # pragma: no cover - depends on environment
    raise SystemExit(
        "PyTorch is required for this optional pipeline. Install it with:\n"
        "  pip install -r requirements/full.txt\n"
        "The demo itself uses the NumPy LSTM: python -m ml.src.lite train"
    ) from exc

from common.env import load_dotenv
from common.jsonutil import write_json
from common.logging_utils import configure_logging
from ml.src.config import (
    FEATURE_SCALER_FILENAME,
    METADATA_FILENAME,
    MODEL_CONFIG_FILENAME,
    MODEL_FILENAME,
    TARGET_SCALER_FILENAME,
    TrainingConfig,
    artifacts_dir_from_env,
    data_path_from_env,
    model_dir_from_env,
)
from ml.src.data_generator import generate_synthetic_dataset
from ml.src.dataset import DatasetBundle, build_datasets
from ml.src.metrics import per_horizon_report, regression_report
from ml.src.model import LSTMForecaster, count_parameters, predict_array, resolve_device, save_model
from ml.src.plots import plot_loss_curves
from ml.src.preprocessing import inverse_scale

logger = logging.getLogger(__name__)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_or_generate_data(cfg: TrainingConfig, data_path: Path, regenerate: bool) -> pd.DataFrame:
    if regenerate or not data_path.is_file():
        df = generate_synthetic_dataset(cfg.n_series, cfg.series_length, cfg.step_seconds, cfg.seed)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(data_path, index=False)
        return df
    return pd.read_csv(data_path)


def _loader(x: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool, seed: int) -> DataLoader:
    dataset = TensorDataset(torch.from_numpy(x), torch.from_numpy(y))
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle,
                      generator=torch.Generator().manual_seed(seed))


def _evaluate_loss(model: nn.Module, loader: DataLoader, loss_fn: nn.Module, device: torch.device) -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            total += loss_fn(model(xb), yb).item() * len(xb)
            count += len(xb)
    return total / max(count, 1)


def fit_model(bundle: DatasetBundle, cfg: TrainingConfig, device: torch.device
              ) -> tuple[LSTMForecaster, dict[str, list[float]], int]:
    """Adam + ReduceLROnPlateau + gradient clipping + early stopping."""
    model = LSTMForecaster(bundle.x_train.shape[2], cfg.hidden_size, cfg.num_layers, cfg.dropout,
                           cfg.prediction_horizon).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=2)
    loss_fn = nn.MSELoss()
    train_loader = _loader(bundle.x_train, bundle.y_train, cfg.batch_size, True, cfg.seed)
    val_loader = _loader(bundle.x_val, bundle.y_val, cfg.batch_size, False, cfg.seed)
    history: dict[str, list[float]] = {"train_loss": [], "val_loss": [], "lr": []}
    best_state = copy.deepcopy(model.state_dict())
    best_val, best_epoch, bad = float("inf"), 0, 0
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        running, seen = 0.0, 0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            optimizer.step()
            running += loss.item() * len(xb)
            seen += len(xb)
        train_loss = running / max(seen, 1)
        val_loss = _evaluate_loss(model, val_loader, loss_fn, device)
        scheduler.step(val_loss)
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["lr"].append(optimizer.param_groups[0]["lr"])
        logger.info("epoch complete", extra={"epoch": epoch, "train_loss": round(train_loss, 6),
                                             "val_loss": round(val_loss, 6)})
        if val_loss < best_val - 1e-7:
            best_val, best_epoch, bad = val_loss, epoch, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            bad += 1
            if bad >= cfg.patience:
                break
    model.load_state_dict(best_state)
    model.eval()
    return model, history, best_epoch


def evaluate_on_test(model: LSTMForecaster, bundle: DatasetBundle, device: torch.device) -> dict[str, Any]:
    pred = np.maximum(inverse_scale(bundle.target_scaler, predict_array(model, bundle.x_test, device)), 0.0)
    actual = inverse_scale(bundle.target_scaler, bundle.y_test)
    per_step = per_horizon_report(actual, pred)
    return {
        "overall": regression_report(actual, pred),
        "one_step": regression_report(actual[:, 0], pred[:, 0]),
        "per_horizon": per_step,
        "uncertainty_rmse": max(step["rmse"] for step in per_step),
    }


def train(cfg: TrainingConfig, data_path: Path, model_dir: Path, artifacts_dir: Path,
          regenerate_data: bool = False) -> dict[str, Any]:
    """Full pipeline; returns the metadata written to ``training_metadata.json``."""
    cfg.validate()
    set_seed(cfg.seed)
    device = resolve_device(cfg.device)
    started = time.perf_counter()
    df = load_or_generate_data(cfg, data_path, regenerate_data)
    bundle = build_datasets(df, cfg)
    model, history, best_epoch = fit_model(bundle, cfg, device)
    report = evaluate_on_test(model, bundle, device)
    model_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    save_model(model, model_dir / MODEL_FILENAME)
    joblib.dump(bundle.feature_scaler, model_dir / FEATURE_SCALER_FILENAME)
    joblib.dump(bundle.target_scaler, model_dir / TARGET_SCALER_FILENAME)
    cfg.save(model_dir / MODEL_CONFIG_FILENAME)
    pd.DataFrame({"epoch": np.arange(1, len(history["train_loss"]) + 1), **history}).to_csv(
        artifacts_dir / "training_history.csv", index=False)
    plot_loss_curves(history, artifacts_dir / "loss_curves.png")
    metadata: dict[str, Any] = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_seconds": round(time.perf_counter() - started, 2),
        "device": str(device),
        "best_epoch": best_epoch,
        "epochs_run": len(history["train_loss"]),
        "parameters": count_parameters(model),
        "dataset": {"path": data_path.name, "sha256": file_sha256(data_path) if data_path.is_file() else None,
                    "rows": len(df), "kind": "synthetic (generic patterns)", **bundle.info},
        "test_metrics": report,
        "history": history,
        "versions": {"python": platform.python_version(), "torch": torch.__version__,
                     "numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
        "config": cfg.to_dict(),
    }
    write_json(model_dir / METADATA_FILENAME, metadata)
    return metadata


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Train the PyTorch LSTM workload forecaster.")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--regenerate-data", action="store_true")
    for flag, typ in (("--sequence-length", int), ("--hidden-size", int), ("--num-layers", int),
                      ("--dropout", float), ("--learning-rate", float), ("--epochs", int),
                      ("--batch-size", int), ("--patience", int), ("--n-series", int),
                      ("--series-length", int), ("--seed", int), ("--device", str)):
        parser.add_argument(flag, type=typ)
    parser.add_argument("--horizon", type=int, dest="prediction_horizon")
    parser.add_argument("--quick", action="store_true", help="small dataset/model for a fast smoke test")
    args = parser.parse_args(argv)
    configure_logging()
    cfg = TrainingConfig.from_env()
    if args.quick:
        cfg.n_series, cfg.series_length, cfg.epochs, cfg.hidden_size = 20, 600, 5, 16
    for name in ("sequence_length", "prediction_horizon", "hidden_size", "num_layers", "dropout",
                 "learning_rate", "epochs", "batch_size", "patience", "n_series", "series_length",
                 "seed", "device"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(cfg, name, value)
    cfg.validate()
    data_path = args.data or data_path_from_env()
    if args.quick and args.data is None:
        data_path = data_path.with_name("synthetic_workload_quick.csv")
    meta = train(cfg, data_path, args.model_dir or model_dir_from_env(),
                 args.artifacts_dir or artifacts_dir_from_env(), args.regenerate_data)
    o = meta["test_metrics"]["overall"]
    print(f"Test MAE={o['mae']:.3f}  RMSE={o['rmse']:.3f}  MAPE={o['mape']:.2f}%  (best epoch {meta['best_epoch']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
