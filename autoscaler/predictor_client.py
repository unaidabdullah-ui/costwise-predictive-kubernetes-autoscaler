"""Prediction layer used by the controller: *what will the workload be?*

``LocalPredictor`` runs a :class:`ml.src.forecasters.Forecaster` in-process (the
NumPy LSTM by default). If the model cannot be loaded it stays *unavailable*,
retries periodically and raises :class:`PredictionUnavailableError`, so the
controller switches to its clearly labelled safe mode instead of crashing.
:meth:`LocalPredictor.reload` hot-swaps a retrained model.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import requests

from ml.src.forecasters import Forecaster, ForecastError, ModelNotAvailableError

logger = logging.getLogger(__name__)


class PredictionUnavailableError(RuntimeError):
    """No valid forecast could be produced this iteration."""


class PredictorClient(Protocol):
    @property
    def horizon(self) -> int: ...

    @property
    def sequence_length(self) -> int: ...

    @property
    def uncertainty(self) -> float | None: ...

    @property
    def available(self) -> bool: ...

    def predict(self, history: Sequence[float] | np.ndarray) -> np.ndarray: ...

    def info(self) -> dict[str, Any]: ...


ForecasterFactory = Callable[[], Forecaster]


class LocalPredictor:
    """In-process forecaster with lazy (re)loading and output validation."""

    def __init__(self, factory: ForecasterFactory, default_horizon: int = 6, default_sequence_length: int = 12,
                 reload_interval_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._factory = factory
        self._default_horizon = default_horizon
        self._default_sequence_length = default_sequence_length
        self._reload_interval = reload_interval_seconds
        self._clock = clock
        self._forecaster: Forecaster | None = None
        self._lock = threading.RLock()
        self._last_attempt: float | None = None
        self.last_error: str | None = None
        self.loaded_at: float | None = None
        self._try_load(force=True)

    def _try_load(self, force: bool = False) -> None:
        now = self._clock()
        if not force and self._last_attempt is not None and now - self._last_attempt < self._reload_interval:
            return
        self._last_attempt = now
        try:
            forecaster = self._factory()
        except (ModelNotAvailableError, ImportError, OSError, ValueError, KeyError) as exc:
            with self._lock:
                self._forecaster = None
            self.last_error = str(exc)
            logger.warning("Forecaster unavailable; controller uses safe mode", extra={"error": str(exc)})
            return
        with self._lock:
            self._forecaster = forecaster
        self.last_error = None
        self.loaded_at = time.time()
        logger.info("Forecaster ready", extra={"forecaster": forecaster.name})

    def reload(self) -> bool:
        """Load the model again now (after RETRAIN MODEL). Keeps the old one on failure."""
        old = self._forecaster
        try:
            forecaster = self._factory()
        except (ModelNotAvailableError, ImportError, OSError, ValueError, KeyError) as exc:
            self.last_error = f"reload failed, previous model kept: {exc}"
            logger.error("model reload failed", extra={"error": str(exc)})
            return False
        with self._lock:
            self._forecaster = forecaster
        self.last_error = None
        self.loaded_at = time.time()
        logger.info("Forecaster reloaded", extra={"had_previous": old is not None})
        return True

    @property
    def available(self) -> bool:
        if self._forecaster is None:
            self._try_load()
        return self._forecaster is not None

    @property
    def forecaster(self) -> Forecaster | None:
        return self._forecaster

    @property
    def horizon(self) -> int:
        f = self._forecaster
        return f.horizon if f else self._default_horizon

    @property
    def sequence_length(self) -> int:
        f = self._forecaster
        return f.min_history if f else self._default_sequence_length

    @property
    def uncertainty(self) -> float | None:
        f = self._forecaster
        return f.uncertainty if f else None

    def predict(self, history: Sequence[float] | np.ndarray) -> np.ndarray:
        if not self.available:
            raise PredictionUnavailableError(f"forecast model not loaded: {self.last_error}")
        try:
            with self._lock:
                assert self._forecaster is not None
                return self._forecaster.predict(history)
        except ForecastError as exc:
            raise PredictionUnavailableError(str(exc)) from exc

    def info(self) -> dict[str, Any]:
        f = self._forecaster
        if f is None:
            return {"name": None, "label": None, "available": False, "error": self.last_error}
        return {**f.info(), "available": True, "loaded_at": self.loaded_at}


class RemotePredictor:
    """Forecasts from a remote HTTP endpoint (``POST /prediction`` of the optional FastAPI service)."""

    def __init__(self, base_url: str, horizon: int = 6, sequence_length: int = 12, timeout_seconds: float = 4.0,
                 session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._horizon, self._sequence_length = horizon, sequence_length
        self.timeout = timeout_seconds
        self.session = session or requests.Session()
        self._uncertainty: float | None = None
        self.last_error: str | None = None

    @property
    def horizon(self) -> int:
        return self._horizon

    @property
    def sequence_length(self) -> int:
        return self._sequence_length

    @property
    def uncertainty(self) -> float | None:
        return self._uncertainty

    @property
    def available(self) -> bool:
        try:
            return self.session.get(f"{self.base_url}/health", timeout=self.timeout).ok
        except requests.RequestException:
            return False

    def predict(self, history: Sequence[float] | np.ndarray) -> np.ndarray:
        payload = {"history": [float(v) for v in np.asarray(history).reshape(-1)]}
        try:
            response = self.session.post(f"{self.base_url}/prediction", json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
            forecast = np.asarray(body["forecast"], dtype=float)
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            self.last_error = str(exc)
            raise PredictionUnavailableError(f"remote prediction failed: {exc}") from exc
        if forecast.size == 0 or not np.all(np.isfinite(forecast)) or np.any(forecast < 0):
            raise PredictionUnavailableError("remote prediction returned invalid values")
        self._horizon = int(forecast.size)
        self._uncertainty = body.get("uncertainty_rmse")
        return forecast

    def info(self) -> dict[str, Any]:
        return {"name": "remote", "url": self.base_url, "available": self.available, "error": self.last_error}


def build_local_predictor(forecaster: str, model_path: Path | None, horizon: int, sequence_length: int
                          ) -> LocalPredictor:
    """:class:`LocalPredictor` for ``lstm_lite`` (default), ``lstm`` or a baseline."""
    from ml.src.forecasters import load_forecaster

    return LocalPredictor(lambda: load_forecaster(forecaster, model_path, horizon, sequence_length),
                          default_horizon=horizon, default_sequence_length=sequence_length)
