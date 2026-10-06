"""Online forecast-accuracy tracking (live MAE / RMSE / confidence).

Every iteration registers its forecast for ``t + k·interval`` (k = 1..H) together
with the load measured when the forecast was made (the *naive* "traffic stays the
same" forecast). When an observation arrives, forecasts whose target time is
within half an interval "mature" and both errors are recorded. Rolling errors
feed the dashboard, the optimiser's uncertainty ``σ`` and the *confidence* check
that switches the controller to safe mode when the model is clearly wrong.

Confidence (a forecast skill score, 0..1)::

    confidence = 1 − MAE_model / (MAE_naive + tolerance)

``tolerance`` is one pod's worth of traffic (errors smaller than that cannot
change the pod count). The model is trusted while it is clearly better than -
or at least not much worse than - assuming traffic stays the same; a broken or
stale model scores near 0 and triggers safe mode. Ramps, where the naive forecast
is badly wrong, do not unfairly penalise the model.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class MaturedForecast:
    target_time: float
    steps_ahead: int
    predicted: float
    actual: float

    @property
    def error(self) -> float:
        return self.predicted - self.actual


class ForecastTracker:
    """Pairs past forecasts with actual observations."""

    def __init__(self, interval_seconds: float, window: int = 24, max_pending: int = 1000) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        self.interval = interval_seconds
        self.window = window
        self._pending: deque[tuple[float, int, float, float | None]] = deque(maxlen=max_pending)
        self._errors: dict[int, deque[float]] = {}
        self._naive_errors: dict[int, deque[float]] = {}
        self._actuals: deque[float] = deque(maxlen=window)
        self.last_matured: list[MaturedForecast] = []

    def reset(self) -> None:
        self._pending.clear()
        self._errors.clear()
        self._naive_errors.clear()
        self._actuals.clear()
        self.last_matured = []

    def register(self, made_at: float, forecast: list[float] | tuple[float, ...],
                 baseline: float | None = None) -> None:
        """Remember a forecast; ``baseline`` is the load measured when it was made (naive forecast)."""
        for k, value in enumerate(forecast, start=1):
            self._pending.append((made_at + k * self.interval, k, float(value),
                                  None if baseline is None else float(baseline)))

    def observe(self, at: float, actual: float) -> list[MaturedForecast]:
        tolerance = self.interval / 2.0
        matured: list[MaturedForecast] = []
        keep: deque[tuple[float, int, float, float | None]] = deque(maxlen=self._pending.maxlen)
        for target, k, predicted, baseline in self._pending:
            if abs(target - at) <= tolerance:
                m = MaturedForecast(target, k, predicted, float(actual))
                matured.append(m)
                self._errors.setdefault(k, deque(maxlen=self.window)).append(m.error)
                if baseline is not None:
                    self._naive_errors.setdefault(k, deque(maxlen=self.window)).append(baseline - float(actual))
            elif target > at - tolerance:
                keep.append((target, k, predicted, baseline))
        self._pending = keep
        self._actuals.append(float(actual))
        self.last_matured = matured
        return matured

    def _errs(self, steps_ahead: int) -> list[float]:
        return list(self._errors.get(steps_ahead, ()))

    def mae(self, steps_ahead: int = 1) -> float | None:
        errs = self._errs(steps_ahead)
        return sum(abs(e) for e in errs) / len(errs) if errs else None

    def rmse(self, steps_ahead: int = 1) -> float | None:
        errs = self._errs(steps_ahead)
        return math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else None

    def last_error(self, steps_ahead: int = 1) -> float | None:
        errs = self._errs(steps_ahead)
        return errs[-1] if errs else None

    def sample_count(self, steps_ahead: int = 1) -> int:
        return len(self._errs(steps_ahead))

    def worst_rmse(self, min_samples: int = 5) -> float | None:
        values = [self.rmse(k) for k, errs in self._errors.items() if len(errs) >= min_samples]
        finite = [v for v in values if v is not None]
        return max(finite) if finite else None

    def naive_mae(self, steps_ahead: int = 1) -> float | None:
        errs = list(self._naive_errors.get(steps_ahead, ()))
        return sum(abs(e) for e in errs) / len(errs) if errs else None

    def confidence(self, steps_ahead: int | None = None, min_samples: int = 6, tolerance: float = 0.0,
                   floor_load: float = 3.0) -> float | None:
        """Forecast skill in [0, 1] (``None`` until ``min_samples`` forecasts matured).

        Uses the furthest horizon step with enough samples (the forecast that drives
        scaling). With naive baselines: ``1 − MAE / (MAE_naive + tolerance)``.
        Without baselines (older callers): ``1 − MAE / max(mean load, floor_load)``.
        """
        steps = [steps_ahead] if steps_ahead else sorted(self._errors, reverse=True)
        for k in steps:
            errs = self._errs(k)
            if len(errs) < min_samples or not self._actuals:
                continue
            mae = sum(abs(e) for e in errs) / len(errs)
            naive = list(self._naive_errors.get(k, ()))
            if len(naive) >= min_samples:
                naive_mae = sum(abs(e) for e in naive) / len(naive)
                denom = naive_mae + max(tolerance, 1e-9)
                return max(0.0, min(1.0, 1.0 - mae / denom))
            mean_load = max(sum(self._actuals) / len(self._actuals), floor_load)
            return max(0.0, 1.0 - mae / mean_load)
        return None
