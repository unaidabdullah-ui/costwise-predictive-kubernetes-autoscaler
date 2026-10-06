"""Common forecaster interface, classical baselines and the forecaster factory.

The autoscaler and the experiments depend only on :class:`Forecaster`, so the
NumPy LSTM (``lstm_lite``, the demo default), the PyTorch LSTM (``lstm``) and
the baselines are interchangeable. Baselines are also used to check that the
LSTM actually beats simple methods - an important sanity check for any model.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np


class ForecastError(RuntimeError):
    """Base class for forecasting failures."""


class InsufficientHistoryError(ForecastError):
    """Fewer observations are available than the model requires."""


class ModelNotAvailableError(ForecastError):
    """A trained model cannot be loaded."""


class Forecaster(ABC):
    """Predicts the next ``horizon`` request-rate values from recent history."""

    name: str = "forecaster"
    label: str = "Forecaster"

    def __init__(self, horizon: int, sequence_length: int) -> None:
        if horizon < 1 or sequence_length < 1:
            raise ValueError("horizon and sequence_length must be >= 1")
        self.horizon = horizon
        self.sequence_length = sequence_length

    @property
    def min_history(self) -> int:
        """Minimum number of observations required by :meth:`predict`."""
        return self.sequence_length

    @property
    def uncertainty(self) -> float | None:
        """Expected forecast RMSE (req/s) if known from offline evaluation."""
        return None

    def predict(self, history: Sequence[float] | np.ndarray) -> np.ndarray:
        """Validate ``history`` then return a ``(horizon,)`` non-negative forecast."""
        arr = validate_history(history, self.min_history)
        out = np.asarray(self._predict(arr), dtype=float).reshape(-1)
        if out.shape != (self.horizon,):
            raise ForecastError(f"{self.name} returned shape {out.shape}, expected ({self.horizon},)")
        if not np.all(np.isfinite(out)):
            raise ForecastError(f"{self.name} produced non-finite values")
        return np.maximum(out, 0.0)

    @abstractmethod
    def _predict(self, history: np.ndarray) -> np.ndarray:
        """Model-specific forecast on a validated 1-D history array."""

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "horizon": self.horizon,
            "sequence_length": self.sequence_length,
            "uncertainty_rmse": self.uncertainty,
        }


def validate_history(history: Sequence[float] | np.ndarray, min_length: int) -> np.ndarray:
    """Return ``history`` as a float array after checking length and finiteness."""
    arr = np.asarray(history, dtype=float).reshape(-1)
    if arr.size < min_length:
        raise InsufficientHistoryError(f"need at least {min_length} observations, got {arr.size}")
    if not np.all(np.isfinite(arr)):
        raise ForecastError("history contains NaN or infinite values")
    if np.any(arr < 0):
        raise ForecastError("history contains negative request rates")
    return arr


class NaiveForecaster(Forecaster):
    """Persistence: every future value equals the last observation."""

    name = "naive"
    label = "Naive (last value)"

    def __init__(self, horizon: int, sequence_length: int = 1) -> None:
        super().__init__(horizon, sequence_length)

    def _predict(self, history: np.ndarray) -> np.ndarray:
        return np.full(self.horizon, history[-1])


class MovingAverageForecaster(Forecaster):
    """Flat forecast equal to the mean of the last ``window`` observations."""

    name = "moving_average"
    label = "Moving average"

    def __init__(self, horizon: int, window: int = 6) -> None:
        super().__init__(horizon, window)
        self.window = window

    def _predict(self, history: np.ndarray) -> np.ndarray:
        return np.full(self.horizon, float(np.mean(history[-self.window :])))


class HoltForecaster(Forecaster):
    """Damped Holt (double exponential smoothing): extrapolates level + trend."""

    name = "holt"
    label = "Holt (exponential smoothing)"

    def __init__(
        self, horizon: int, sequence_length: int = 12, alpha: float = 0.5, beta: float = 0.3,
        damping: float = 0.9,
    ) -> None:
        super().__init__(horizon, sequence_length)
        if not (0 < alpha <= 1 and 0 < beta <= 1 and 0 < damping <= 1):
            raise ValueError("alpha, beta, damping must be in (0, 1]")
        self.alpha, self.beta, self.damping = alpha, beta, damping

    def _predict(self, history: np.ndarray) -> np.ndarray:
        window = history[-self.sequence_length :]
        level = window[0]
        trend = window[1] - window[0] if window.size > 1 else 0.0
        phi = self.damping
        for y in window[1:]:
            prev = level
            level = self.alpha * y + (1 - self.alpha) * (level + phi * trend)
            trend = self.beta * (level - prev) + (1 - self.beta) * phi * trend
        return level + trend * np.cumsum(phi ** np.arange(1, self.horizon + 1))


BASELINE_NAMES: tuple[str, ...] = ("naive", "moving_average", "holt")
FORECASTER_NAMES: tuple[str, ...] = ("lstm_lite", "lstm", *BASELINE_NAMES)


def build_baseline(name: str, horizon: int, sequence_length: int) -> Forecaster:
    """Factory for baseline forecasters by name."""
    if name == "naive":
        return NaiveForecaster(horizon)
    if name == "moving_average":
        return MovingAverageForecaster(horizon, window=max(2, min(6, sequence_length)))
    if name == "holt":
        return HoltForecaster(horizon, sequence_length=max(4, min(12, sequence_length)))
    raise ValueError(f"Unknown baseline forecaster {name!r}")


def load_forecaster(
    name: str,
    model_path: Path | None = None,
    horizon: int = 6,
    sequence_length: int = 12,
) -> Forecaster:
    """Return a forecaster by name (lazy imports keep NumPy-only installs working).

    * ``lstm_lite`` - NumPy LSTM from ``model_path`` (a ``.npz`` file; default ``ml/models/lstm_lite.npz``)
    * ``lstm``      - PyTorch LSTM from the directory ``model_path`` (needs torch + pandas)
    * ``naive`` / ``moving_average`` / ``holt`` - baselines
    """
    if name == "lstm_lite":
        from ml.src.lite import LiteLSTMForecaster

        return LiteLSTMForecaster.load(model_path)
    if name == "lstm":
        try:
            from ml.src.predict import WorkloadPredictor
        except ImportError as exc:  # torch / pandas missing
            raise ModelNotAvailableError(f"PyTorch pipeline not installed: {exc}") from exc
        return WorkloadPredictor.load(model_path)
    if name in BASELINE_NAMES:
        return build_baseline(name, horizon, sequence_length)
    raise ValueError(f"Unknown forecaster {name!r}; choose one of {FORECASTER_NAMES}")


def finite_or_none(value: float | None) -> float | None:
    """``value`` unless it is ``None``/NaN/inf (JSON-friendly)."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)
