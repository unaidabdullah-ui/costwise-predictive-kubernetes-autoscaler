"""Reproducible synthetic workload dataset generator (generic patterns).

Generates independent series ("series"), each following one pattern family with
randomised parameters: constant, linear_growth, linear_decrease, sinusoidal,
periodic, morning_evening, spikes, bursty, ramp_peak and mixed.

For every timestamp it emits::

    series_id, pattern, timestamp, request_rate, cpu_utilization,
    memory_utilization, replicas, latency_ms

``request_rate`` is the forecasting target; the other columns are derived with
the analytical model in :mod:`common.system_model` and a lagged reactive replica
policy so the data resembles what Prometheus records.

    python -m ml.src.data_generator --n-series 40 --length 1440 --seed 42
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from common import patterns as p
from common import system_model as sm
from common.logging_utils import configure_logging

logger = logging.getLogger(__name__)

PATTERNS: tuple[str, ...] = (
    "constant",
    "linear_growth",
    "linear_decrease",
    "sinusoidal",
    "periodic",
    "morning_evening",
    "spikes",
    "bursty",
    "ramp_peak",
    "mixed",
)

MAX_RATE = 160.0
DEFAULT_CAPACITY_PER_POD = 20.0
DEFAULT_TARGET_UTILIZATION = 0.7
DEFAULT_MAX_REPLICAS = 10

PatternFn = Callable[[np.ndarray, np.random.Generator], np.ndarray]


def _step_seconds(t: np.ndarray) -> float:
    return float(t[1] - t[0]) if t.size > 1 else 1.0


def _constant(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return p.constant(t, rng.uniform(5, 120))


def _linear_growth(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    total = float(t[-1])
    t0 = rng.uniform(0.0, 0.3) * total
    t1 = t0 + rng.uniform(0.2, 0.6) * total
    return p.linear(t, rng.uniform(5, 40), rng.uniform(60, 140), t0, t1)


def _linear_decrease(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    total = float(t[-1])
    t0 = rng.uniform(0.0, 0.3) * total
    t1 = t0 + rng.uniform(0.2, 0.6) * total
    return p.linear(t, rng.uniform(60, 140), rng.uniform(5, 40), t0, t1)


def _sinusoidal(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    base = rng.uniform(30, 80)
    period = rng.uniform(20, 240) * _step_seconds(t)
    return p.sinusoid(t, base, rng.uniform(10, base - 5), period, phase=rng.uniform(0, 2 * np.pi))


def _periodic(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    period = rng.uniform(40, 200) * _step_seconds(t)
    base = rng.uniform(35, 75)
    return p.sinusoid(t, base, rng.uniform(10, 30), period) + p.sinusoid(
        t, 0.0, rng.uniform(3, 12), period / 2.0, rng.uniform(0, 6)
    )


def _morning_evening(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    period = float(t[-1]) / rng.uniform(1.0, 3.0)
    return p.daily_double_peak(
        t, rng.uniform(8, 25), rng.uniform(40, 100), rng.uniform(30, 90), period
    )


def _spikes(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = p.constant(t, rng.uniform(10, 40))
    step = _step_seconds(t)
    for _ in range(int(rng.integers(2, 6))):
        ramp = 0.0 if rng.random() < 0.4 else rng.uniform(2, 12) * step
        out = out + p.spike(
            t,
            start=rng.uniform(0, float(t[-1])),
            duration=rng.uniform(5, 30) * step,
            height=rng.uniform(30, 90),
            ramp=ramp,
        )
    return out


def _bursty(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    step = _step_seconds(t)
    return p.constant(t, rng.uniform(15, 40)) + p.bursts(
        t, rng, int(rng.integers(4, 12)), (20.0, 70.0), (3 * step, 15 * step)
    )


def _ramp_peak(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Repeated baseline → smooth rise → plateau → smooth fall episodes."""
    step = _step_seconds(t)
    out = p.constant(t, rng.uniform(8, 25))
    cursor = rng.uniform(5, 40) * step
    while cursor < float(t[-1]):
        rise, hold, fall = (rng.uniform(10, 30) * step, rng.uniform(8, 25) * step,
                            rng.uniform(8, 25) * step)
        height = rng.uniform(30, 110)
        out = out + p.smooth_step(t, 0.0, height, cursor, cursor + rise)
        out = out + p.smooth_step(t, 0.0, -height, cursor + rise + hold, cursor + rise + hold + fall)
        cursor += rise + hold + fall + rng.uniform(20, 80) * step
    return out


def _mixed(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    total, step = float(t[-1]), _step_seconds(t)
    trend = p.linear(t, rng.uniform(10, 40), rng.uniform(30, 80), 0.0, total)
    season = p.sinusoid(t, 0.0, rng.uniform(5, 25), rng.uniform(40, 200) * step)
    extra = p.bursts(t, rng, int(rng.integers(1, 5)), (15.0, 50.0), (4 * step, 20 * step))
    return trend + season + extra


PATTERN_FUNCTIONS: dict[str, PatternFn] = {
    "constant": _constant,
    "linear_growth": _linear_growth,
    "linear_decrease": _linear_decrease,
    "sinusoidal": _sinusoidal,
    "periodic": _periodic,
    "morning_evening": _morning_evening,
    "spikes": _spikes,
    "bursty": _bursty,
    "ramp_peak": _ramp_peak,
    "mixed": _mixed,
}


def simulate_reactive_replicas(
    rate: np.ndarray,
    capacity_per_pod: float = DEFAULT_CAPACITY_PER_POD,
    target_utilization: float = DEFAULT_TARGET_UTILIZATION,
    max_replicas: int = DEFAULT_MAX_REPLICAS,
    lag_steps: int = 2,
) -> np.ndarray:
    """Replica count of a simple reactive scaler that observes load ``lag_steps`` late."""
    lagged = np.concatenate([np.full(lag_steps, rate[0]), rate[:-lag_steps]]) if lag_steps else rate
    needed = np.ceil(lagged / (capacity_per_pod * target_utilization))
    return np.clip(needed, 1, max_replicas).astype(int)


def generate_series(
    pattern: str,
    length: int,
    step_seconds: float,
    rng: np.random.Generator,
    noise_range: tuple[float, float] = (0.02, 0.10),
) -> np.ndarray:
    """One request-rate series of ``length`` samples for ``pattern``."""
    if pattern not in PATTERN_FUNCTIONS:
        raise KeyError(f"Unknown pattern {pattern!r}; choose from {PATTERNS}")
    if length < 2:
        raise ValueError("length must be >= 2")
    t = np.arange(length, dtype=float) * step_seconds
    base = PATTERN_FUNCTIONS[pattern](t, rng)
    noisy = base + p.noise(t, rng, rng.uniform(*noise_range), np.maximum(base, 1.0))
    return np.clip(noisy, 0.0, MAX_RATE)


def derive_system_columns(
    rate: np.ndarray, rng: np.random.Generator, capacity_per_pod: float = DEFAULT_CAPACITY_PER_POD
) -> dict[str, np.ndarray]:
    """Derive replicas / CPU / memory / latency columns from a request-rate series."""
    replicas = simulate_reactive_replicas(rate, capacity_per_pod)
    rho = sm.utilisation(rate, replicas, capacity_per_pod)
    backlog = np.convolve(np.maximum(rho - 1.0, 0.0), np.ones(4) / 4.0, mode="same")
    cpu = sm.cpu_percent(rho) + rng.normal(0, 1.5, size=rate.shape)
    mem = sm.memory_percent(rho) + rng.normal(0, 1.0, size=rate.shape)
    latency = sm.latency_ms(rho, backlog_s=backlog) * rng.lognormal(0, 0.05, size=rate.shape)
    return {
        "cpu_utilization": np.clip(cpu, 0, 100).round(2),
        "memory_utilization": np.clip(mem, 0, 100).round(2),
        "replicas": replicas,
        "latency_ms": latency.round(2),
    }


def generate_synthetic_dataset(
    n_series: int = 40,
    length: int = 1440,
    step_seconds: float = 15.0,
    seed: int = 42,
    patterns: tuple[str, ...] = PATTERNS,
    start: datetime | None = None,
):  # -> pandas.DataFrame (pandas imported lazily: the demo control plane does not need it)
    """Multi-series synthetic workload dataset (byte-for-byte reproducible for a seed)."""
    import pandas as pd

    if n_series < 1:
        raise ValueError("n_series must be >= 1")
    rng = np.random.default_rng(seed)
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    frames = []
    for idx in range(n_series):
        pattern = patterns[idx % len(patterns)]
        rate = generate_series(pattern, length, step_seconds, rng)
        cols = derive_system_columns(rate, rng)
        timestamps = pd.date_range(start + timedelta(days=idx), periods=length, freq=f"{int(step_seconds)}s")
        frames.append(
            pd.DataFrame(
                {"series_id": idx, "pattern": pattern, "timestamp": timestamps,
                 "request_rate": rate.round(3), **cols}
            )
        )
    df = pd.concat(frames, ignore_index=True)
    logger.info("Generated synthetic dataset", extra={"rows": len(df), "series": n_series})
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic workload dataset.")
    parser.add_argument("--n-series", type=int, default=40)
    parser.add_argument("--length", type=int, default=1440, help="samples per series")
    parser.add_argument("--step-seconds", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    configure_logging()
    from ml.src.config import data_path_from_env

    output = args.output or data_path_from_env()
    output.parent.mkdir(parents=True, exist_ok=True)
    df = generate_synthetic_dataset(args.n_series, args.length, args.step_seconds, args.seed)
    df.to_csv(output, index=False)
    print(f"Wrote {len(df):,} rows ({args.n_series} series) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
