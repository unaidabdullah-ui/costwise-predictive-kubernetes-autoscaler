"""Cleaning, normalisation and sliding-window sequence creation (PyTorch pipeline).

    raw data → clean → split chronologically → fit scaler on TRAIN only
             → transform → sliding windows (X: seq_len × features, y: horizon)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

NUMERIC_COLUMNS: tuple[str, ...] = (
    "request_rate", "cpu_utilization", "memory_utilization", "replicas", "latency_ms",
)


def clean_workload(
    df: pd.DataFrame,
    value_columns: tuple[str, ...] = NUMERIC_COLUMNS,
    max_gap: int = 4,
    group_column: str | None = "series_id",
) -> pd.DataFrame:
    """Parse/sort timestamps, drop duplicates, interpolate short gaps, clip negatives."""
    if "timestamp" not in df.columns or "request_rate" not in df.columns:
        raise ValueError("DataFrame must contain 'timestamp' and 'request_rate' columns")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["timestamp"])
    present = [c for c in value_columns if c in out.columns]
    for col in present:
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    has_group = group_column is not None and group_column in out.columns
    keys = [group_column, "timestamp"] if has_group else ["timestamp"]
    out = out.sort_values(keys, kind="mergesort").drop_duplicates(subset=keys, keep="last")

    def _fill(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.copy()
        frame[present] = frame[present].interpolate(limit=max_gap, limit_direction="both")
        return frame

    out = pd.concat([_fill(g) for _, g in out.groupby(group_column, sort=True)]) if has_group else _fill(out)
    out = out.dropna(subset=["request_rate"])
    for col in present:
        out[col] = out[col].clip(lower=0.0)
    return out.reset_index(drop=True)


@dataclass(frozen=True)
class SplitBounds:
    """Index boundaries of a chronological train/validation/test split."""

    train_end: int
    val_end: int
    total: int

    @property
    def train(self) -> slice:
        return slice(0, self.train_end)

    @property
    def val(self) -> slice:
        return slice(self.train_end, self.val_end)

    @property
    def test(self) -> slice:
        return slice(self.val_end, self.total)


def chronological_split(n: int, train_ratio: float = 0.7, val_ratio: float = 0.15) -> SplitBounds:
    """Split ``n`` ordered samples into train/val/test without shuffling (no leakage)."""
    if n < 3:
        raise ValueError("need at least 3 samples to split")
    if not (0 < train_ratio < 1 and 0 < val_ratio < 1 and train_ratio + val_ratio < 1):
        raise ValueError("invalid split ratios")
    return SplitBounds(int(n * train_ratio), int(n * (train_ratio + val_ratio)), n)


def fit_scaler(values: np.ndarray) -> MinMaxScaler:
    """Fit a [0, 1] Min-Max scaler on training data only."""
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.shape[0] == 0:
        raise ValueError("cannot fit scaler on empty data")
    return MinMaxScaler(feature_range=(0.0, 1.0)).fit(arr)


def scale(scaler: MinMaxScaler, values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 1:
        return scaler.transform(arr.reshape(-1, 1)).ravel().astype(np.float32)
    return scaler.transform(arr).astype(np.float32)


def inverse_scale(scaler: MinMaxScaler, values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return scaler.inverse_transform(arr.reshape(-1, 1)).ravel().reshape(arr.shape)


def create_sequences(
    features: np.ndarray, target: np.ndarray, sequence_length: int, horizon: int
) -> tuple[np.ndarray, np.ndarray]:
    """Sliding windows: ``X[i] = features[i:i+L]``, ``y[i] = target[i+L:i+L+H]``."""
    feats = np.asarray(features, dtype=np.float32)
    tgt = np.asarray(target, dtype=np.float32)
    if feats.ndim != 2:
        raise ValueError("features must be 2-D (T, F)")
    if tgt.ndim != 1 or tgt.shape[0] != feats.shape[0]:
        raise ValueError("target must be 1-D and aligned with features")
    if sequence_length < 1 or horizon < 1:
        raise ValueError("sequence_length and horizon must be >= 1")
    n = feats.shape[0] - sequence_length - horizon + 1
    if n <= 0:
        return (np.empty((0, sequence_length, feats.shape[1]), dtype=np.float32),
                np.empty((0, horizon), dtype=np.float32))
    x_idx = np.arange(sequence_length)[None, :] + np.arange(n)[:, None]
    y_idx = np.arange(horizon)[None, :] + np.arange(n)[:, None] + sequence_length
    return feats[x_idx], tgt[y_idx]
