"""Inference for the forecasters (PyTorch ``WorkloadPredictor`` + a CLI for all of them).

    python -m ml.src.predict --scenario flash_sale --at 120                 # demo NumPy LSTM
    python -m ml.src.predict --values 8,8,9,10,12,15,19,24,30,36,41,47
    python -m ml.src.predict --scenario flash_sale --at 120 --forecaster holt
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from common.env import load_dotenv
from common.logging_utils import configure_logging
from ml.src.config import (
    FEATURE_SCALER_FILENAME,
    METADATA_FILENAME,
    MODEL_CONFIG_FILENAME,
    MODEL_FILENAME,
    TARGET_SCALER_FILENAME,
    TrainingConfig,
    model_dir_from_env,
)
from ml.src.forecasters import FORECASTER_NAMES, Forecaster, ModelNotAvailableError, load_forecaster

logger = logging.getLogger(__name__)


class WorkloadPredictor(Forecaster):
    """PyTorch LSTM loaded from a model directory (optional pipeline)."""

    name = "lstm"
    label = "LSTM (PyTorch)"

    def __init__(self, model: Any, feature_scaler: Any, target_scaler: Any, config: TrainingConfig,
                 metadata: dict[str, Any] | None = None, device: str = "cpu") -> None:
        super().__init__(config.prediction_horizon, config.sequence_length)
        self.model, self.feature_scaler, self.target_scaler = model, feature_scaler, target_scaler
        self.config, self.metadata, self.device = config, metadata or {}, device

    @classmethod
    def load(cls, model_dir: Path | None = None, device: str = "cpu") -> WorkloadPredictor:
        model_dir = model_dir or model_dir_from_env()
        required = [MODEL_FILENAME, FEATURE_SCALER_FILENAME, TARGET_SCALER_FILENAME, MODEL_CONFIG_FILENAME]
        missing = [n for n in required if not (model_dir / n).is_file()]
        if missing:
            raise ModelNotAvailableError(f"PyTorch model artifacts missing in {model_dir}: {missing}")
        try:
            import joblib

            from ml.src.model import load_model
        except ImportError as exc:  # pragma: no cover
            raise ModelNotAvailableError(f"PyTorch/joblib not installed: {exc}") from exc
        config = TrainingConfig.load(model_dir / MODEL_CONFIG_FILENAME)
        meta_path = model_dir / METADATA_FILENAME
        metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        try:
            model = load_model(model_dir / MODEL_FILENAME, device=device)
            fs, ts = joblib.load(model_dir / FEATURE_SCALER_FILENAME), joblib.load(model_dir / TARGET_SCALER_FILENAME)
        except Exception as exc:
            raise ModelNotAvailableError(f"Failed to load model artifacts: {exc}") from exc
        if model.n_features != len(config.feature_columns) or model.horizon != config.prediction_horizon:
            raise ModelNotAvailableError("model weights do not match model_config.json")
        return cls(model, fs, ts, config, metadata, device)

    @property
    def uncertainty(self) -> float | None:
        try:
            return float(self.metadata["test_metrics"]["uncertainty_rmse"])
        except (KeyError, TypeError, ValueError):
            return None

    def _predict(self, history: np.ndarray) -> np.ndarray:
        import torch

        from ml.src.features import feature_matrix

        lookback = self.config.sequence_length + self.config.rolling_window
        feats = feature_matrix(history[-lookback:], self.config.feature_columns,
                               self.config.rolling_window)[-self.config.sequence_length :]
        scaled = self.feature_scaler.transform(feats).astype(np.float32)
        with torch.no_grad():
            out = self.model(torch.from_numpy(scaled).unsqueeze(0).to(self.device)).cpu().numpy()
        return self.target_scaler.inverse_transform(out.reshape(-1, 1)).ravel()

    def info(self) -> dict[str, Any]:
        base = super().info()
        test = self.metadata.get("test_metrics", {}).get("overall", {})
        base.update({"trained_at": self.metadata.get("trained_at"), "test_mae": test.get("mae"),
                     "test_rmse": test.get("rmse"), "step_seconds": self.config.step_seconds})
        return base


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Forecast future workload.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--values", type=str, help="comma-separated request rates, oldest first")
    src.add_argument("--scenario", type=str, help="history from a named rate scenario (simulator.scenarios)")
    parser.add_argument("--at", type=float, default=120.0, help="scenario time (s) to forecast from")
    parser.add_argument("--forecaster", default="lstm_lite", choices=FORECASTER_NAMES)
    parser.add_argument("--model", type=Path, default=None, help="model file/dir (default: shipped model)")
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    forecaster = load_forecaster(args.forecaster, args.model)
    step = float(forecaster.info().get("step_seconds") or 5.0)
    actual: np.ndarray | None = None
    if args.values:
        history = np.array([float(v) for v in args.values.split(",") if v.strip()])
    else:
        from simulator.scenarios import get_scenario

        profile = get_scenario(args.scenario).measured_profile(step_s=step)
        idx = int(args.at // step)
        if idx < forecaster.min_history:
            parser.error(f"--at too early: need {forecaster.min_history * step:.0f} s of history")
        history, actual = profile[:idx], profile[idx : idx + forecaster.horizon]
    forecast = forecaster.predict(history)
    print(f"forecaster : {forecaster.info().get('label', forecaster.name)}")
    print(f"last value : {history[-1]:.2f} req/s")
    for h, value in enumerate(forecast, start=1):
        line = f"t+{h * step:>4.0f} s   : {value:8.2f} req/s"
        if actual is not None and h <= len(actual):
            line += f"   (actual {actual[h - 1]:.2f})"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
