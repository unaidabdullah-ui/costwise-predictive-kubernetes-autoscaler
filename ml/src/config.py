"""Configuration of the PyTorch training pipeline (``python -m ml.src.train``).

Values come from (increasing priority) dataclass defaults, environment variables
(``.env`` supported) and CLI flags. The configuration actually used is written
next to the model (``model_config.json``) so inference always uses the same
sequence length, horizon and features.

The live demo uses the lighter NumPy LSTM in :mod:`ml.src.lite`; this pipeline
is kept for the larger offline experiments and as the reference implementation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from common.env import get_float, get_int, get_str

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_PATH = PROJECT_ROOT / "ml" / "data" / "synthetic_workload.csv"
DEFAULT_MODEL_DIR = PROJECT_ROOT / "ml" / "models"
DEFAULT_ARTIFACTS_DIR = PROJECT_ROOT / "ml" / "artifacts"

MODEL_FILENAME = "lstm_forecaster.pt"
FEATURE_SCALER_FILENAME = "feature_scaler.joblib"
TARGET_SCALER_FILENAME = "target_scaler.joblib"
MODEL_CONFIG_FILENAME = "model_config.json"
METADATA_FILENAME = "training_metadata.json"


def resolve_path(value: str | Path, default: Path) -> Path:
    """Resolve a (possibly relative) path against the project root."""
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass
class TrainingConfig:
    """Hyper-parameters and data settings for the PyTorch LSTM forecaster."""

    sequence_length: int = 24
    prediction_horizon: int = 4
    step_seconds: int = 15
    target_column: str = "request_rate"
    feature_columns: list[str] = field(
        default_factory=lambda: ["request_rate", "rate_diff", "rate_rolling_mean", "rate_rolling_std"]
    )
    rolling_window: int = 6
    hidden_size: int = 32
    num_layers: int = 2
    dropout: float = 0.2
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    epochs: int = 20
    batch_size: int = 64
    patience: int = 5
    grad_clip: float = 1.0
    train_ratio: float = 0.70
    val_ratio: float = 0.15
    n_series: int = 30
    series_length: int = 960
    seed: int = 42
    device: str = "auto"

    def validate(self) -> None:
        """Raise ``ValueError`` if the configuration is inconsistent."""
        if self.sequence_length < 2:
            raise ValueError("sequence_length must be >= 2")
        if self.prediction_horizon < 1:
            raise ValueError("prediction_horizon must be >= 1")
        if self.hidden_size < 1 or self.num_layers < 1:
            raise ValueError("hidden_size and num_layers must be >= 1")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if self.epochs < 1 or self.batch_size < 1:
            raise ValueError("epochs and batch_size must be >= 1")
        if not (0 < self.train_ratio < 1 and 0 < self.val_ratio < 1):
            raise ValueError("train_ratio and val_ratio must be in (0, 1)")
        if self.train_ratio + self.val_ratio >= 1:
            raise ValueError("train_ratio + val_ratio must be < 1 (test split needed)")
        if self.target_column not in self.feature_columns:
            raise ValueError("target_column must be one of feature_columns")
        if self.rolling_window < 1:
            raise ValueError("rolling_window must be >= 1")

    @classmethod
    def from_env(cls) -> TrainingConfig:
        """Build a configuration from environment variables."""
        cfg = cls(
            sequence_length=get_int("TORCH_SEQUENCE_LENGTH", cls.sequence_length),
            prediction_horizon=get_int("TORCH_PREDICTION_HORIZON", cls.prediction_horizon),
            step_seconds=int(get_float("TORCH_STEP_SECONDS", cls.step_seconds)),
            hidden_size=get_int("LSTM_HIDDEN_SIZE", cls.hidden_size),
            num_layers=get_int("LSTM_NUM_LAYERS", cls.num_layers),
            dropout=get_float("LSTM_DROPOUT", cls.dropout),
            learning_rate=get_float("LEARNING_RATE", cls.learning_rate),
            epochs=get_int("EPOCHS", cls.epochs),
            batch_size=get_int("BATCH_SIZE", cls.batch_size),
            seed=get_int("SEED", cls.seed),
            device=get_str("TORCH_DEVICE", cls.device),
        )
        cfg.validate()
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainingConfig:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> TrainingConfig:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def model_dir_from_env() -> Path:
    """Directory holding the trained PyTorch model, scalers and metadata."""
    return resolve_path(get_str("TORCH_MODEL_DIR", ""), DEFAULT_MODEL_DIR)


def artifacts_dir_from_env() -> Path:
    return resolve_path(get_str("ARTIFACTS_DIR", ""), DEFAULT_ARTIFACTS_DIR)


def data_path_from_env() -> Path:
    return resolve_path(get_str("DATA_PATH", ""), DEFAULT_DATA_PATH)
