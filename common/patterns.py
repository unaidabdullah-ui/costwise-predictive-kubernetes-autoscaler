"""Vectorised workload pattern primitives.

Shared by the synthetic training-data generators (``ml.src.data_generator``,
``ml.src.lite``), the rate scenarios used by the offline cluster simulator
(``simulator.scenarios``) and the live shopper plans
(``workload_generator.plans``), so that the patterns the forecaster is trained
on and the patterns used in the demo come from the same code.

All functions take an array ``t`` of times in seconds and return an array of the
same shape.
"""

from __future__ import annotations

import numpy as np


def constant(t: np.ndarray, level: float) -> np.ndarray:
    """Flat workload at ``level``."""
    return np.full_like(t, float(level), dtype=float)


def linear(t: np.ndarray, start: float, end: float, t0: float, t1: float) -> np.ndarray:
    """Linear change from ``start`` (before ``t0``) to ``end`` (after ``t1``)."""
    if t1 <= t0:
        raise ValueError("t1 must be greater than t0")
    frac = np.clip((t - t0) / (t1 - t0), 0.0, 1.0)
    return start + (end - start) * frac


def smooth_step(t: np.ndarray, start: float, end: float, t0: float, t1: float) -> np.ndarray:
    """Cosine-shaped (S-curve) transition from ``start`` to ``end`` between ``t0`` and ``t1``."""
    if t1 <= t0:
        raise ValueError("t1 must be greater than t0")
    frac = np.clip((t - t0) / (t1 - t0), 0.0, 1.0)
    return start + (end - start) * (1.0 - np.cos(np.pi * frac)) / 2.0


def sinusoid(
    t: np.ndarray, base: float, amplitude: float, period: float, phase: float = 0.0
) -> np.ndarray:
    """``base + amplitude * sin(2*pi*t/period + phase)``."""
    if period <= 0:
        raise ValueError("period must be positive")
    return base + amplitude * np.sin(2.0 * np.pi * t / period + phase)


def daily_double_peak(
    t: np.ndarray,
    base: float,
    morning_amp: float,
    evening_amp: float,
    period: float,
    morning_pos: float = 0.375,
    evening_pos: float = 0.79,
    width: float = 0.06,
) -> np.ndarray:
    """Morning/evening pattern repeated every ``period`` seconds (two Gaussian bumps)."""
    if period <= 0:
        raise ValueError("period must be positive")
    phase = np.mod(t, period) / period

    def bump(center: float) -> np.ndarray:
        d = np.minimum(np.abs(phase - center), 1.0 - np.abs(phase - center))
        return np.exp(-0.5 * (d / width) ** 2)

    return base + morning_amp * bump(morning_pos) + evening_amp * bump(evening_pos)


def spike(
    t: np.ndarray, start: float, duration: float, height: float, ramp: float = 0.0
) -> np.ndarray:
    """Additive spike of ``height`` from ``start`` for ``duration`` seconds.

    ``ramp`` seconds of linear rise/fall make it trapezoidal; ``ramp=0`` is an
    instantaneous (unpredictable) step.
    """
    out = np.zeros_like(t, dtype=float)
    end = start + duration
    if ramp > 0:
        rise = np.clip((t - (start - ramp)) / ramp, 0.0, 1.0)
        fall = np.clip(((end + ramp) - t) / ramp, 0.0, 1.0)
        return height * np.minimum(rise, fall)
    out[(t >= start) & (t < end)] = height
    return out


def bursts(
    t: np.ndarray,
    rng: np.random.Generator,
    n_bursts: int,
    height: tuple[float, float],
    duration: tuple[float, float],
) -> np.ndarray:
    """Additive random bursts (random start, height and duration)."""
    out = np.zeros_like(t, dtype=float)
    if n_bursts <= 0 or t.size == 0:
        return out
    t_min, t_max = float(np.min(t)), float(np.max(t))
    for _ in range(n_bursts):
        start = rng.uniform(t_min, t_max)
        dur = rng.uniform(*duration)
        h = rng.uniform(*height)
        out += spike(t, start, dur, h, ramp=min(dur / 4.0, 15.0))
    return out


def noise(
    t: np.ndarray, rng: np.random.Generator, relative_std: float, signal: np.ndarray
) -> np.ndarray:
    """Additive term ``signal * N(0, relative_std)`` (multiplicative noise)."""
    if relative_std <= 0:
        return np.zeros_like(t, dtype=float)
    return signal * rng.normal(0.0, relative_std, size=t.shape)


def trailing_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Mean of the last ``window`` samples at every position (what ``rate()[w]`` reports)."""
    if window < 1:
        raise ValueError("window must be >= 1")
    v = np.asarray(values, dtype=float)
    csum = np.concatenate([[0.0], np.cumsum(v)])
    idx = np.arange(1, v.size + 1)
    lo = np.maximum(0, idx - window)
    return (csum[idx] - csum[lo]) / (idx - lo)
