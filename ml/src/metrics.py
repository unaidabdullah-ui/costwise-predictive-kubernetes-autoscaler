"""Forecast accuracy metrics (MAE, RMSE, MAPE, sMAPE) in original units (req/s)."""

from __future__ import annotations

import numpy as np


def _arrays(actual: np.ndarray, predicted: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    if a.shape != p.shape:
        raise ValueError(f"shape mismatch: actual {a.shape} vs predicted {p.shape}")
    if a.size == 0:
        raise ValueError("empty arrays")
    return a, p


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    a, p = _arrays(actual, predicted)
    return float(np.mean(np.abs(a - p)))


def rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    a, p = _arrays(actual, predicted)
    return float(np.sqrt(np.mean((a - p) ** 2)))


def mape(actual: np.ndarray, predicted: np.ndarray, min_actual: float = 1.0) -> float:
    """Mean absolute percentage error (%) over samples with ``|actual| >= min_actual``.

    Near-zero actuals are excluded because percentage errors explode there; returns
    ``nan`` when no sample qualifies (shown as "not valid" on the dashboard).
    """
    a, p = _arrays(actual, predicted)
    mask = np.abs(a) >= min_actual
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.abs((a[mask] - p[mask]) / a[mask])) * 100.0)


def smape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Symmetric MAPE (%), bounded in [0, 200]."""
    a, p = _arrays(actual, predicted)
    denom = np.abs(a) + np.abs(p)
    mask = denom > 0
    if not np.any(mask):
        return 0.0
    return float(np.mean(2.0 * np.abs(a[mask] - p[mask]) / denom[mask]) * 100.0)


def regression_report(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {"mae": mae(actual, predicted), "rmse": rmse(actual, predicted),
            "mape": mape(actual, predicted), "smape": smape(actual, predicted)}


def per_horizon_report(actual: np.ndarray, predicted: np.ndarray) -> list[dict[str, float]]:
    """Metrics for each forecast step of ``(N, H)`` arrays."""
    a, p = _arrays(actual, predicted)
    if a.ndim != 2:
        raise ValueError("expected 2-D (N, H) arrays")
    return [{"step": h + 1, **regression_report(a[:, h], p[:, h])} for h in range(a.shape[1])]
