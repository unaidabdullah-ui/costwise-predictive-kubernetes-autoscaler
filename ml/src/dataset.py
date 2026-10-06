"""Turn a (multi-series) workload frame into train/validation/test windows.

Each series is split chronologically (70/15/15). Scalers are fit on the training
portions only; windows never cross series or split boundaries (no leakage).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

from ml.src.config import TrainingConfig
from ml.src.features import feature_matrix
from ml.src.preprocessing import SplitBounds, chronological_split, clean_workload, create_sequences, fit_scaler, scale


@dataclass
class DatasetBundle:
    """Windowed, scaled datasets plus the fitted scalers."""

    x_train: np.ndarray
    y_train: np.ndarray
    x_val: np.ndarray
    y_val: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    feature_scaler: MinMaxScaler
    target_scaler: MinMaxScaler
    test_series_ids: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    info: dict[str, int] = field(default_factory=dict)


def _series_frames(df: pd.DataFrame) -> list[pd.DataFrame]:
    if "series_id" in df.columns:
        return [g.reset_index(drop=True) for _, g in df.groupby("series_id", sort=True)]
    return [df.reset_index(drop=True)]


def build_datasets(df: pd.DataFrame, cfg: TrainingConfig) -> DatasetBundle:
    """Clean, split, scale and window ``df`` according to ``cfg``."""
    cfg.validate()
    clean = clean_workload(df)
    min_len = cfg.sequence_length + cfg.prediction_horizon
    series = [s for s in _series_frames(clean) if len(s) >= 3 * min_len]
    if not series:
        raise ValueError(f"No series long enough: need >= {3 * min_len} samples per series")

    per_series: list[tuple[int, np.ndarray, np.ndarray, SplitBounds]] = []
    train_feats, train_target = [], []
    target_idx = cfg.feature_columns.index(cfg.target_column)
    for s in series:
        feats = feature_matrix(s[cfg.target_column], cfg.feature_columns, cfg.rolling_window)
        target = feats[:, target_idx].copy()
        bounds = chronological_split(len(s), cfg.train_ratio, cfg.val_ratio)
        sid = int(s["series_id"].iloc[0]) if "series_id" in s.columns else 0
        per_series.append((sid, feats, target, bounds))
        train_feats.append(feats[bounds.train])
        train_target.append(target[bounds.train])

    feature_scaler = fit_scaler(np.concatenate(train_feats))
    target_scaler = fit_scaler(np.concatenate(train_target))

    xs: dict[str, list[np.ndarray]] = {"train": [], "val": [], "test": []}
    ys: dict[str, list[np.ndarray]] = {"train": [], "val": [], "test": []}
    test_ids: list[np.ndarray] = []
    for sid, feats, target, bounds in per_series:
        f_scaled, t_scaled = scale(feature_scaler, feats), scale(target_scaler, target)
        for name, slc in (("train", bounds.train), ("val", bounds.val), ("test", bounds.test)):
            x, y = create_sequences(f_scaled[slc], t_scaled[slc], cfg.sequence_length, cfg.prediction_horizon)
            xs[name].append(x)
            ys[name].append(y)
            if name == "test":
                test_ids.append(np.full(len(x), sid, dtype=int))

    def _cat(parts: list[np.ndarray], tail: tuple[int, ...]) -> np.ndarray:
        parts = [p for p in parts if len(p)]
        return np.concatenate(parts).astype(np.float32) if parts else np.empty((0, *tail), dtype=np.float32)

    shape = (cfg.sequence_length, len(cfg.feature_columns))
    bundle = DatasetBundle(
        x_train=_cat(xs["train"], shape), y_train=_cat(ys["train"], (cfg.prediction_horizon,)),
        x_val=_cat(xs["val"], shape), y_val=_cat(ys["val"], (cfg.prediction_horizon,)),
        x_test=_cat(xs["test"], shape), y_test=_cat(ys["test"], (cfg.prediction_horizon,)),
        feature_scaler=feature_scaler, target_scaler=target_scaler,
        test_series_ids=np.concatenate(test_ids) if test_ids else np.empty(0, dtype=int),
    )
    bundle.info = {
        "series_used": len(series),
        "train_windows": len(bundle.x_train),
        "val_windows": len(bundle.x_val),
        "test_windows": len(bundle.x_test),
    }
    if not (len(bundle.x_train) and len(bundle.x_val) and len(bundle.x_test)):
        raise ValueError(f"Empty split after windowing: {bundle.info}")
    return bundle
