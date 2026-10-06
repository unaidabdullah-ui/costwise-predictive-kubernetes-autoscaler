"""Matplotlib plots for training and evaluation reports (headless, optional)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_loss_curves(history: Mapping[str, Sequence[float]], path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    epochs = np.arange(1, len(history["train_loss"]) + 1)
    ax.plot(epochs, history["train_loss"], label="training loss", marker="o", ms=3)
    ax.plot(epochs, history["val_loss"], label="validation loss", marker="o", ms=3)
    best = int(np.argmin(history["val_loss"])) + 1
    ax.axvline(best, color="grey", lw=1, label=f"best epoch ({best})")
    ax.set(xlabel="epoch", ylabel="MSE (scaled)", title="LSTM training and validation loss")
    ax.legend()
    ax.grid(alpha=0.3)
    return _save(fig, path)


def plot_actual_vs_predicted(actual: np.ndarray, predicted: np.ndarray, path: Path, title: str,
                             max_points: int = 600) -> Path:
    n = min(len(actual), max_points)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(np.arange(n), actual[:n], label="actual", lw=1.4)
    ax.plot(np.arange(n), predicted[:n], label="predicted", lw=1.2, alpha=0.85)
    ax.set(xlabel="time step", ylabel="request rate (req/s)", title=title)
    ax.legend()
    ax.grid(alpha=0.3)
    return _save(fig, path)


def plot_horizon_error(per_step: Mapping[str, Sequence[float]], path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name, values in per_step.items():
        ax.plot(np.arange(1, len(values) + 1), values, marker="o", label=name)
    ax.set(xlabel="forecast horizon (steps ahead)", ylabel="MAE (req/s)", title="Forecast error vs horizon")
    ax.legend()
    ax.grid(alpha=0.3)
    return _save(fig, path)


def plot_error_distribution(errors: np.ndarray, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(errors, bins=60, alpha=0.85)
    ax.axvline(0, color="black", lw=1)
    ax.set(xlabel="forecast error (predicted - actual, req/s)", ylabel="count",
           title="Distribution of one-step forecast errors")
    ax.grid(alpha=0.3)
    return _save(fig, path)


def plot_model_comparison(rows: Sequence[Mapping[str, float]], path: Path) -> Path:
    names = [str(r["model"]) for r in rows]
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(x - 0.2, [r["mae"] for r in rows], width=0.4, label="MAE")
    ax.bar(x + 0.2, [r["rmse"] for r in rows], width=0.4, label="RMSE")
    ax.set_xticks(x, names)
    ax.set(ylabel="req/s", title="Test-set error: LSTM vs baselines")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    return _save(fig, path)
