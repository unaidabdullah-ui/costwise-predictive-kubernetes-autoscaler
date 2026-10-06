"""Named, reproducible request-rate scenarios (offline experiments and model checks).

A :class:`Scenario` maps elapsed seconds to an offered request rate (req/s). The
live dashboard uses *shopper plans* (:mod:`workload_generator.plans`); the
``flash_sale``, ``busy`` and ``burst`` scenarios here are built from exactly those
plans (shoppers × 0.04 req/s), so offline experiments replay the live demo's
traffic shapes.

Rates are sized for the laptop demo: one marketplace pod serves ~20 req/s
(250m CPU), i.e. 14 req/s at the 70 % target utilisation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from common import patterns as p

RateFunction = Callable[[np.ndarray, np.random.Generator], np.ndarray]
RATE_PER_SHOPPER = 0.04


@dataclass(frozen=True)
class Scenario:
    """A deterministic workload profile."""

    name: str
    description: str
    duration_s: int
    rate_fn: RateFunction = field(repr=False)
    noise_std: float = 0.05
    min_rate: float = 0.5

    def times(self, step_s: float = 1.0) -> np.ndarray:
        return np.arange(0.0, float(self.duration_s), step_s)

    def profile(self, step_s: float = 1.0, seed: int = 42, scale: float = 1.0) -> np.ndarray:
        """Offered load sampled every ``step_s`` seconds (fully determined by the arguments)."""
        if step_s <= 0:
            raise ValueError("step_s must be positive")
        if scale <= 0:
            raise ValueError("scale must be positive")
        rng = np.random.default_rng(seed)
        t = self.times(step_s)
        base = self.rate_fn(t, rng)
        noisy = base + p.noise(t, rng, self.noise_std, base)
        return np.maximum(noisy * scale, self.min_rate * scale)

    def measured_profile(self, step_s: float = 5.0, seed: int = 42, window_s: int = 30) -> np.ndarray:
        """What Prometheus would report: Poisson arrivals → ``rate()[30s]`` every ``step_s``."""
        per_second = self.profile(1.0, seed)
        counts = np.random.default_rng(seed + 1).poisson(per_second).astype(float)
        return p.trailing_mean(counts, window_s)[:: int(step_s)]


def _plan_rate(plan_names: tuple[tuple[str, float], ...]) -> RateFunction:
    """Chain shopper plans: ((name, seconds), ...) → rate function."""

    def fn(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        from workload_generator.plans import Plan

        values = np.zeros_like(t, dtype=float)
        shoppers, offset = 0.0, 0.0
        boundaries = []
        plans = []
        for name, seconds in plan_names:
            plan = Plan(name, shoppers)
            plans.append((offset, plan))
            shoppers = plan.shoppers(seconds)
            offset += seconds
            boundaries.append(offset)
        for i, ti in enumerate(t):
            start, plan = plans[-1]
            for (s, pl), end in zip(plans, boundaries, strict=True):
                if ti < end:
                    start, plan = s, pl
                    break
            values[i] = plan.shoppers(float(ti - start)) * RATE_PER_SHOPPER
        return values

    return fn


def _constant(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return p.constant(t, 30.0)


def _ramp_up(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return p.linear(t, 8.0, 60.0, 120.0, 600.0)


def _ramp_down(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return p.linear(t, 60.0, 8.0, 120.0, 600.0)


def _spike(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    # sudden step with no precursor: the hardest case for any forecaster
    return p.constant(t, 15.0) + p.spike(t, start=240.0, duration=150.0, height=45.0, ramp=0.0)


def _periodic(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return p.sinusoid(t, base=35.0, amplitude=25.0, period=300.0, phase=-np.pi / 2)


def _bursty(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return p.constant(t, 18.0) + p.bursts(t, rng, n_bursts=5, height=(20.0, 35.0), duration=(40.0, 100.0))


def _mixed(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    trend = p.linear(t, 12.0, 30.0, 0.0, max(float(t.max(initial=0.0)), 1.0))
    season = p.sinusoid(t, 0.0, 12.0, 240.0)
    return trend + season + p.spike(t, start=480.0, duration=90.0, height=25.0, ramp=45.0)


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("constant", "Stable workload at ~30 req/s.", 600, _constant),
        Scenario("ramp_up", "Gradual increase from 8 to 60 req/s over 8 minutes.", 720, _ramp_up),
        Scenario("ramp_down", "Gradual decrease from 60 to 8 req/s.", 720, _ramp_down),
        Scenario("spike", "Sudden step from 15 to 60 req/s with no warning.", 600, _spike),
        Scenario("periodic", "Sinusoidal load between 10 and 60 req/s (5-minute period).", 900, _periodic),
        Scenario("bursty", "Baseline 18 req/s with random bursts.", 900, _bursty, noise_std=0.08),
        Scenario("mixed", "Upward trend + seasonality + ramped spike.", 900, _mixed),
        Scenario(
            "flash_sale",
            "The dashboard's FLASH SALE: normal shoppers, announcement, rush to ~64 req/s, cool-down.",
            600,
            _plan_rate((("normal", 120.0), ("flash_sale", 300.0), ("stopped", 180.0))),
            noise_std=0.03,
        ),
        Scenario(
            "busy",
            "The dashboard's BUSY TRAFFIC: gradual rise to ~36 req/s, then recovery.",
            600,
            _plan_rate((("normal", 90.0), ("busy", 330.0), ("recovery", 180.0))),
            noise_std=0.03,
        ),
        Scenario(
            "burst",
            "The dashboard's burst plan: four short intense bursts on a 10 req/s baseline.",
            420,
            _plan_rate((("normal", 60.0), ("burst", 300.0), ("normal", 60.0))),
            noise_std=0.03,
        ),
    )
}

# Scenarios used by the experiment suite (stable, spike, gradual increase, periodic, bursty + flash sale).
EXPERIMENT_SCENARIOS: tuple[str, ...] = ("constant", "spike", "ramp_up", "periodic", "bursty", "flash_sale")


def get_scenario(name: str) -> Scenario:
    """Scenario by name (``KeyError`` lists the valid names)."""
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown scenario {name!r}. Available: {sorted(SCENARIOS)}") from exc
