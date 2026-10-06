"""Open-loop HTTP traffic driver (used by the CLI load tools).

"Open loop": requests are scheduled at the target rate regardless of how fast the
server answers - like real users. A closed-loop tester would slow down when the
server is overloaded and hide the very problem autoscaling must solve.

Every request uses a new TCP connection (``Connection: close``): Kubernetes
Services balance *connections*, so keep-alive would pin traffic to the pods that
existed when the test started and hide newly added replicas.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests


@dataclass
class IntervalStats:
    second: int
    target_rps: float
    sent: int = 0
    ok: int = 0
    errors: int = 0
    dropped: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, float]:
        lat = sorted(self.latencies_ms)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else float("nan")
        return {
            "second": self.second, "target_rps": round(self.target_rps, 3), "sent": self.sent, "ok": self.ok,
            "errors": self.errors, "dropped": self.dropped,
            "mean_latency_ms": sum(lat) / len(lat) if lat else float("nan"), "p95_latency_ms": p95,
        }


class TrafficDriver:
    """Send GET requests to ``url`` following a per-second rate."""

    def __init__(self, url: str, max_in_flight: int = 64, timeout_s: float = 5.0, keepalive: bool = False) -> None:
        self.url = url
        self.timeout = timeout_s
        self.headers = {} if keepalive else {"Connection": "close"}
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_in_flight, thread_name_prefix="load")

    def _one(self, stats: IntervalStats) -> None:
        start = time.perf_counter()
        try:
            resp = requests.get(self.url, timeout=self.timeout, headers=self.headers)
            latency = (time.perf_counter() - start) * 1000.0
            with self._lock:
                if resp.status_code < 400:
                    stats.ok += 1
                    stats.latencies_ms.append(latency)
                else:
                    stats.errors += 1
        except requests.RequestException:
            with self._lock:
                stats.errors += 1
        finally:
            self._slots.release()

    def run_second(self, second: int, rate: float, carry: float = 0.0) -> tuple[IntervalStats, float]:
        """Schedule ``rate`` requests evenly over one second (fractions carried over)."""
        stats = IntervalStats(second=second, target_rps=rate)
        total = rate + carry
        n = int(total)
        t0 = time.perf_counter()
        for i in range(n):
            delay = t0 + i / max(n, 1) - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            if self._slots.acquire(blocking=False):
                with self._lock:
                    stats.sent += 1
                self._executor.submit(self._one, stats)
            else:
                with self._lock:
                    stats.dropped += 1
        remaining = 1.0 - (time.perf_counter() - t0)
        if remaining > 0:
            time.sleep(remaining)
        return stats, total - n

    def close(self) -> None:
        self._executor.shutdown(wait=True)
