"""Constant-rate load test and per-pod capacity calibration.

``CAPACITY_PER_POD`` (req/s one store pod serves at 100 % of its CPU limit) is
the most important planning parameter - measure it on *your* laptop::

    kubectl -n costwise scale deployment/marketplace --replicas=1
    python -m simulator.load_test --find-capacity --url http://127.0.0.1:8080/api/products

The search raises the rate step by step until p95 latency exceeds the SLO or
more than 1 % of requests fail; the last healthy rate is the capacity.
"""

from __future__ import annotations

import argparse
import os
import time

from simulator.traffic import IntervalStats, TrafficDriver


def run_constant(driver: TrafficDriver, rps: float, duration: int) -> dict[str, float]:
    stats: list[IntervalStats] = []
    carry = 0.0
    for second in range(duration):
        s, carry = driver.run_second(second, rps, carry)
        stats.append(s)
    time.sleep(min(driver.timeout, 5.0))
    lat = sorted(x for s in stats for x in s.latencies_ms)
    sent = sum(s.sent for s in stats)
    dropped = sum(s.dropped for s in stats)
    errors = sum(s.errors for s in stats)

    def pct(q: float) -> float:
        return lat[min(len(lat) - 1, int(q * len(lat)))] if lat else float("nan")

    return {"target_rps": rps, "achieved_rps": sum(s.ok for s in stats) / duration, "sent": sent,
            "errors": errors, "dropped": dropped, "error_rate": (errors + dropped) / max(sent + dropped, 1),
            "p50_ms": pct(0.5), "p95_ms": pct(0.95), "p99_ms": pct(0.99)}


def find_capacity(driver: TrafficDriver, start_rps: float, step_rps: float, max_rps: float, step_duration: int,
                  latency_slo_ms: float) -> tuple[float, list[dict[str, float]]]:
    results: list[dict[str, float]] = []
    healthy, rps = 0.0, start_rps
    while rps <= max_rps:
        res = run_constant(driver, rps, step_duration)
        results.append(res)
        ok = res["p95_ms"] <= latency_slo_ms and res["error_rate"] <= 0.01
        print(f"{rps:6.1f} req/s -> achieved {res['achieved_rps']:6.1f}, p95 {res['p95_ms']:7.1f} ms, "
              f"errors {100 * res['error_rate']:.1f}%  {'OK' if ok else 'SLO BREACHED'}")
        if not ok:
            break
        healthy = rps
        rps += step_rps
    return healthy, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Constant-rate load test / capacity finder.")
    parser.add_argument("--url", default=os.getenv("STORE_URL", "http://127.0.0.1:8080") + "/api/products")
    parser.add_argument("--rps", type=float, default=15.0)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--find-capacity", action="store_true")
    parser.add_argument("--start-rps", type=float, default=5.0)
    parser.add_argument("--step-rps", type=float, default=5.0)
    parser.add_argument("--max-rps", type=float, default=120.0)
    parser.add_argument("--step-duration", type=int, default=20)
    parser.add_argument("--latency-slo-ms", type=float, default=250.0)
    args = parser.parse_args(argv)
    driver = TrafficDriver(args.url)
    try:
        if args.find_capacity:
            healthy, _ = find_capacity(driver, args.start_rps, args.step_rps, args.max_rps, args.step_duration,
                                       args.latency_slo_ms)
            print(f"\nMeasured capacity ~ {healthy:.1f} req/s for the current number of pods.")
            print("Divide by the pod count and set CAPACITY_PER_POD in .env.")
        else:
            for key, value in run_constant(driver, args.rps, args.duration).items():
                print(f"{key:>12}: {value:.2f}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
