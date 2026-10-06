"""Shopper plans: how many simulated shoppers are active over time.

A *plan* maps the seconds elapsed since it was started to a number of active
shoppers. Every plan starts from the shopper count that was active when it was
selected, so switching scenarios never jumps abruptly. The traffic engine turns
shoppers into real HTTP requests at ``WORKLOAD_RATE`` requests per shopper per
second (default 0.04: one page every 25 s).

The same plans are used (via :mod:`ml.src.lite`) to generate the labelled
*bootstrap* training data, so the forecaster has seen these traffic *shapes*
before - but not your measured traffic, which is added by RETRAIN MODEL.

Pure Python on purpose: the traffic-generator image does not need NumPy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DEFAULT_RATE_PER_SHOPPER = 0.04  # requests per second per active shopper

NORMAL_SHOPPERS = 170
BUSY_SHOPPERS = 900
FLASH_PEAK_SHOPPERS = 1600
FLASH_AFTER_SHOPPERS = 450
BURST_BASE_SHOPPERS = 250
BURST_PEAK_SHOPPERS = 1400
RECOVERY_SHOPPERS = 150
GENERATE_USERS_STEP = 300
MAX_SHOPPERS = 4000


def smooth(t: float, start: float, end: float, t0: float, t1: float) -> float:
    """Cosine S-curve from ``start`` (before ``t0``) to ``end`` (after ``t1``)."""
    if t <= t0:
        return start
    if t >= t1:
        return end
    frac = (t - t0) / (t1 - t0)
    return start + (end - start) * (1.0 - math.cos(math.pi * frac)) / 2.0


@dataclass(frozen=True)
class PlanInfo:
    """Human-readable description of a plan (shown on the dashboard)."""

    name: str
    label: str
    description: str
    peak_shoppers: int


PLANS: dict[str, PlanInfo] = {
    p.name: p
    for p in (
        PlanInfo("stopped", "Stopped", "No shoppers: traffic falls to zero within a few seconds.", 0),
        PlanInfo("normal", "Normal", "About 170 shoppers browsing, gently varying (~7 req/s).", 190),
        PlanInfo("busy", "Busy", "Gradual increase to ~900 shoppers over 2.5 minutes (~36 req/s).", 940),
        PlanInfo(
            "flash_sale",
            "Flash sale",
            "Announcement, then a rush to ~1,600 shoppers within a minute (~64 req/s), "
            "a 1.5-minute peak and a cool-down to ~450.",
            FLASH_PEAK_SHOPPERS + 80,
        ),
        PlanInfo("burst", "Burst", "Four short, intense bursts to ~1,400 shoppers on a ~250 baseline.", 1400),
        PlanInfo("recovery", "Recovery", "Traffic fades to a quiet ~150 shoppers over a minute.", 150),
        PlanInfo("users", "Generate users", "Adds 300 shoppers (ramped over 10 s) and holds.", MAX_SHOPPERS),
    )
}


@dataclass
class Plan:
    """A shopper plan started at ``start_shoppers``."""

    name: str
    start_shoppers: float = 0.0
    target: float | None = None  # only for "users"
    phase: float = field(default=0.0)  # deterministic wave phase

    def __post_init__(self) -> None:
        if self.name not in PLANS:
            raise ValueError(f"unknown plan {self.name!r}; choose from {sorted(PLANS)}")
        self.start_shoppers = max(0.0, float(self.start_shoppers))

    @property
    def info(self) -> PlanInfo:
        return PLANS[self.name]

    def shoppers(self, t: float) -> float:
        """Active shoppers ``t`` seconds after the plan started (never negative)."""
        s0 = self.start_shoppers
        wave = math.sin(2 * math.pi * t / 150.0 + self.phase)
        if self.name == "stopped":
            value = smooth(t, s0, 0.0, 0.0, 8.0)
        elif self.name == "normal":
            value = smooth(t, s0, NORMAL_SHOPPERS, 0.0, 20.0) * (1.0 + 0.10 * wave * min(1.0, t / 20.0))
        elif self.name == "busy":
            value = smooth(t, s0, BUSY_SHOPPERS, 0.0, 150.0) * (1.0 + 0.04 * wave * min(1.0, t / 150.0))
        elif self.name == "flash_sale":
            announce = s0 + 80.0
            if t < 20.0:
                value = smooth(t, s0, announce, 0.0, 20.0)
            elif t < 80.0:
                value = smooth(t, announce, FLASH_PEAK_SHOPPERS, 20.0, 80.0)
            elif t < 170.0:
                value = FLASH_PEAK_SHOPPERS * (1.0 + 0.05 * math.sin(2 * math.pi * (t - 80.0) / 45.0))
            else:
                value = smooth(t, FLASH_PEAK_SHOPPERS, FLASH_AFTER_SHOPPERS, 170.0, 260.0)
        elif self.name == "burst":
            value = smooth(t, s0, BURST_BASE_SHOPPERS, 0.0, 10.0)
            for k in range(4):
                b0 = 15.0 + k * 60.0  # each burst: 5 s up, 15 s hold, 5 s down; 35 s quiet between
                if b0 <= t <= b0 + 25.0:
                    up = smooth(t, 0.0, 1.0, b0, b0 + 5.0)
                    down = smooth(t, 1.0, 0.0, b0 + 20.0, b0 + 25.0)
                    value = BURST_BASE_SHOPPERS + (BURST_PEAK_SHOPPERS - BURST_BASE_SHOPPERS) * min(up, down)
        elif self.name == "recovery":
            value = smooth(t, s0, RECOVERY_SHOPPERS, 0.0, 60.0)
        else:  # users
            target = self.target if self.target is not None else s0 + GENERATE_USERS_STEP
            value = smooth(t, s0, min(float(target), MAX_SHOPPERS), 0.0, 10.0)
        return max(0.0, value)

    def describe(self) -> dict[str, object]:
        info = self.info
        return {"name": info.name, "label": info.label, "description": info.description}
