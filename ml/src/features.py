"""Feature engineering for the PyTorch forecaster (pandas-based).

All features derive from the request-rate series only, so they can be recomputed
exactly at inference time from the Prometheus history:
``request_rate``, ``rate_diff``, ``rate_rolling_mean``, ``rate_rolling_std``.
(The NumPy demo forecaster computes the same four features in :mod:`ml.src.lite`.)
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

AVAILABLE_FEATURES: tuple[str, ...] = ("request_rate", "rate_diff", "rate_rolling_mean", "rate_rolling_std")


def build_features(rate: Sequence[float] | np.ndarray | pd.Series, window: int = 6) -> pd.DataFrame:
    """Feature frame for one chronologically ordered series."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = pd.Series(np.asarray(rate, dtype=float))
    roll = series.rolling(window=window, min_periods=1)
    return pd.DataFrame(
        {
            "request_rate": series,
            "rate_diff": series.diff().fillna(0.0),
            "rate_rolling_mean": roll.mean(),
            "rate_rolling_std": roll.std(ddof=0).fillna(0.0),
        }
    )


def feature_matrix(
    rate: Sequence[float] | np.ndarray | pd.Series, feature_columns: Sequence[str], window: int = 6
) -> np.ndarray:
    """``(len(rate), len(feature_columns))`` float32 matrix."""
    unknown = set(feature_columns) - set(AVAILABLE_FEATURES)
    if unknown:
        raise ValueError(f"Unknown feature(s): {sorted(unknown)}")
    return build_features(rate, window=window)[list(feature_columns)].to_numpy(dtype=np.float32)
