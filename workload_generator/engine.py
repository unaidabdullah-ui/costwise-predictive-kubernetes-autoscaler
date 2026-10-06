"""Traffic engine: turns a shopper plan into real HTTP requests against the marketplace.

Open-loop load: every 100 ms the engine computes the active shopper count from
the current :class:`~workload_generator.plans.Plan`, converts it to a request
rate (``shoppers × rate_per_shopper``) and enqueues that many shopper actions
(Poisson-distributed). A fixed pool of worker threads sends them with
``Connection: close`` so the Kubernetes Service spreads requests across *all*
marketplace pods, including newly added ones.

Each action is a real shop request with the configured mix (browse 28 %, product
page 25 %, search 17 %, deals 10 %, categories 7 %, add to cart 8 %, view cart
3 %, checkout 2 %). If the workers cannot keep up (the store is very slow), the
extra actions are counted as ``skipped`` instead of silently disappearing.

Pure standard library: the generator image only needs Starlette + Uvicorn for
its small control API.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import queue
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from common.promtext import Registry
from marketplace.catalog import CATEGORIES, SEARCH_TERMS, products
from workload_generator.plans import DEFAULT_RATE_PER_SHOPPER, MAX_SHOPPERS, PLANS, Plan

logger = logging.getLogger("workload_generator")

ACTION_MIX: tuple[tuple[str, float], ...] = (
    ("browse", 0.28), ("product", 0.25), ("search", 0.17), ("deals", 0.10),
    ("categories", 0.07), ("add_to_cart", 0.08), ("view_cart", 0.03), ("checkout", 0.02),
)
TICK_SECONDS = 0.1


@dataclass
class EngineSettings:
    target_url: str = "http://marketplace:80"
    rate_per_shopper: float = DEFAULT_RATE_PER_SHOPPER
    workers: int = 64
    queue_limit: int = 256
    timeout_s: float = 4.0
    seed: int | None = None


def poisson(rng: random.Random, lam: float) -> int:
    """Poisson sample (Knuth for small λ, normal approximation for large λ)."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


class TrafficEngine:
    """Runs the shopper plan and the HTTP workers; thread-safe control methods."""

    def __init__(self, settings: EngineSettings, registry: Registry | None = None) -> None:
        self.settings = settings
        parts = urlsplit(settings.target_url)
        if parts.scheme not in ("http", "") or not parts.hostname:
            raise ValueError(f"TARGET_URL must be an http:// URL, got {settings.target_url!r}")
        self._host = parts.hostname
        self._port = parts.port or 80
        self._rng = random.Random(settings.seed)
        self._lock = threading.Lock()
        self._plan = Plan("stopped", 0.0)
        self._plan_started = time.monotonic()
        self._shoppers = 0.0
        self._queue: queue.Queue[str] = queue.Queue(maxsize=settings.queue_limit)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._recent: deque[tuple[float, bool, float]] = deque(maxlen=20_000)  # (time, ok, latency)
        self._totals = {"sent": 0, "ok": 0, "errors": 0, "skipped": 0}
        self._product_ids = [p.id for p in products()]
        self._category_ids = [c["id"] for c in CATEGORIES]
        self._actions = [a for a, _ in ACTION_MIX]
        self._weights = [w for _, w in ACTION_MIX]
        self.registry = registry or Registry(process_metrics=True)
        r = self.registry
        self._m_requests = r.counter("generator_requests", "Shopper requests sent to the marketplace.",
                                     ("action", "outcome"))
        self._m_latency = r.histogram("generator_request_duration_seconds",
                                      "Latency seen by simulated shoppers (seconds).",
                                      buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0))
        r.gauge("generator_active_shoppers", "Simulated shoppers currently active.", function=lambda: self._shoppers)
        r.gauge("generator_target_request_rate", "Requests per second the plan asks for.",
                function=lambda: self._shoppers * self.settings.rate_per_shopper)
        r.gauge("generator_queue_depth", "Shopper actions waiting for a worker.", function=self._queue.qsize)
        self._m_scenario = r.gauge("generator_scenario", "1 for the active scenario.", ("scenario",))
        for action in self._actions:
            for outcome in ("ok", "error", "skipped"):
                self._m_requests.labels(action=action, outcome=outcome)
        for name in PLANS:
            self._m_scenario.labels(scenario=name).set(1.0 if name == "stopped" else 0.0)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._threads = [threading.Thread(target=self._scheduler, name="plan-scheduler", daemon=True)]
        self._threads += [threading.Thread(target=self._worker, name=f"shopper-{i}", daemon=True)
                          for i in range(self.settings.workers)]
        for t in self._threads:
            t.start()
        logger.info("traffic engine started: target %s, %d workers, %.3f req/s per shopper",
                    self.settings.target_url, self.settings.workers, self.settings.rate_per_shopper)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads = []

    # ------------------------------------------------------------------ control
    def set_scenario(self, name: str, target: float | None = None) -> dict[str, Any]:
        if name not in PLANS:
            raise ValueError(f"unknown scenario {name!r}; choose from {sorted(PLANS)}")
        with self._lock:
            current = self._current_shoppers_locked()
            if name == "users" and target is None:
                base = self._plan.target if self._plan.name == "users" and self._plan.target else current
                target = min(MAX_SHOPPERS, base + 300)
            self._plan = Plan(name, current, target=target, phase=self._rng.uniform(0, 2 * math.pi))
            self._plan_started = time.monotonic()
            for plan_name in PLANS:
                self._m_scenario.labels(scenario=plan_name).set(1.0 if plan_name == name else 0.0)
        logger.info("scenario -> %s (from %.0f shoppers%s)", name, current,
                    f", target {target:.0f}" if target is not None else "")
        return self.status()

    def add_users(self, count: int = 300) -> dict[str, Any]:
        with self._lock:
            current = self._current_shoppers_locked()
            base = self._plan.target if self._plan.name == "users" and self._plan.target else current
        return self.set_scenario("users", target=min(MAX_SHOPPERS, base + max(1, count)))

    def _current_shoppers_locked(self) -> float:
        return self._plan.shoppers(time.monotonic() - self._plan_started)

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            plan = self._plan
            elapsed = time.monotonic() - self._plan_started
            shoppers = plan.shoppers(elapsed)
            totals = dict(self._totals)
        window = [r for r in list(self._recent) if now - r[0] <= 10.0]
        ok = [r for r in window if r[1]]
        lat = sorted(r[2] for r in ok)
        return {
            "scenario": plan.name, "label": plan.info.label, "description": plan.info.description,
            "scenario_elapsed_s": round(elapsed, 1), "active_shoppers": round(shoppers),
            "target_rps": round(shoppers * self.settings.rate_per_shopper, 2),
            "achieved_rps": round(len(window) / 10.0, 2),
            "success_ratio": round(len(ok) / len(window), 4) if window else None,
            "latency_p50_ms": round(lat[len(lat) // 2] * 1000, 1) if lat else None,
            "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))] * 1000, 1) if lat else None,
            "totals": totals, "queue_depth": self._queue.qsize(), "rate_per_shopper": self.settings.rate_per_shopper,
            "target_url": self.settings.target_url, "scenarios": {k: v.label for k, v in PLANS.items()},
        }

    # ------------------------------------------------------------------ threads
    def _scheduler(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            with self._lock:
                self._shoppers = self._current_shoppers_locked()
            lam = self._shoppers * self.settings.rate_per_shopper * TICK_SECONDS
            for _ in range(poisson(self._rng, lam)):
                action = self._rng.choices(self._actions, self._weights)[0]
                try:
                    self._queue.put_nowait(action)
                except queue.Full:
                    self._record(action, "skipped", 0.0)
            next_tick += TICK_SECONDS
            delay = next_tick - time.monotonic()
            if delay < -1.0:  # fell far behind (laptop paused): resynchronise
                next_tick = time.monotonic()
            elif delay > 0:
                self._stop.wait(delay)

    def _worker(self) -> None:
        rng = random.Random(self._rng.random())
        while not self._stop.is_set():
            try:
                action = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            started = time.perf_counter()
            try:
                status = self._send(action, rng)
                outcome = "ok" if status < 400 or status == 404 else "error"
            except (OSError, http.client.HTTPException):
                outcome = "error"
            self._record(action, outcome, time.perf_counter() - started)

    def _record(self, action: str, outcome: str, latency: float) -> None:
        self._m_requests.labels(action=action, outcome=outcome).inc()
        with self._lock:
            if outcome == "skipped":
                self._totals["skipped"] += 1
                return
            self._totals["sent"] += 1
            self._totals["ok" if outcome == "ok" else "errors"] += 1
        self._recent.append((time.time(), outcome == "ok", latency))
        if outcome == "ok":
            self._m_latency.observe(latency)

    def request_for(self, action: str, rng: random.Random) -> tuple[str, str, bytes | None, dict[str, str]]:
        """(method, path, body, headers) for a shopper action - public for tests."""
        headers: dict[str, str] = {}
        if action == "browse":
            sort = rng.choice(["featured", "rating", "price_asc", "discount"])
            return "GET", f"/api/products?category={rng.choice(self._category_ids)}&sort={sort}", None, headers
        if action == "product":
            return "GET", f"/api/products/{rng.choice(self._product_ids)}", None, headers
        if action == "search":
            return "GET", f"/api/search?q={rng.choice(SEARCH_TERMS)}", None, headers
        if action == "deals":
            return "GET", "/api/deals", None, headers
        if action == "categories":
            return "GET", "/api/categories", None, headers
        cart = ".".join(f"{pid}-{rng.randint(1, 2)}" for pid in rng.sample(self._product_ids, rng.randint(1, 3)))
        headers["Cookie"] = f"cw_cart={cart}"
        if action == "add_to_cart":
            body = json.dumps({"product_id": rng.choice(self._product_ids), "quantity": 1}).encode()
            headers["Content-Type"] = "application/json"
            return "POST", "/api/cart", body, headers
        if action == "view_cart":
            return "GET", "/api/cart", None, headers
        body = json.dumps({"name": "Simulated shopper", "payment": rng.choice(["upi", "card", "cod"])}).encode()
        headers["Content-Type"] = "application/json"
        return "POST", "/api/checkout", body, headers

    def _send(self, action: str, rng: random.Random) -> int:
        method, path, body, headers = self.request_for(action, rng)
        headers.update({"Connection": "close", "User-Agent": "costwise-shopper/2.1"})
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self.settings.timeout_s)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()
