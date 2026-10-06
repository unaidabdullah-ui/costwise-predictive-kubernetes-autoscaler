"""Replay a *rate scenario* against any URL (CLI tool for experiments).

The dashboard's buttons drive the shopper-based traffic generator
(:mod:`workload_generator`); this tool replays the fixed rate scenarios of
:mod:`simulator.scenarios` - useful for repeatable live experiments::

    python -m simulator.workload_generator --list
    python -m simulator.workload_generator --scenario flash_sale --url http://127.0.0.1:8080/api/products
    python simulator/workload_generator.py --scenario spike --scale 0.5 --output spike.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # allow `python simulator/workload_generator.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulator.scenarios import SCENARIOS, get_scenario  # noqa: E402
from simulator.traffic import IntervalStats, TrafficDriver  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate HTTP traffic following a rate scenario.")
    parser.add_argument("--scenario", default="flash_sale")
    parser.add_argument("--url", default=os.getenv("STORE_URL", "http://127.0.0.1:8080") + "/api/products")
    parser.add_argument("--scale", type=float, default=1.0, help="multiply every rate by this factor")
    parser.add_argument("--duration", type=float, default=None, help="override duration (seconds)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-in-flight", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=None, help="per-second statistics CSV")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args(argv)
    if args.list:
        for name, scen in SCENARIOS.items():
            print(f"{name:<11} {scen.duration_s:>5} s  {scen.description}")
        return 0
    scenario = get_scenario(args.scenario)
    profile = scenario.profile(step_s=1.0, seed=args.seed, scale=args.scale)
    if args.duration:
        profile = profile[: int(args.duration)]
    print(f"Scenario '{scenario.name}': {len(profile)} s, {profile.min():.1f}-{profile.max():.1f} req/s -> {args.url}")
    driver = TrafficDriver(args.url, max_in_flight=args.max_in_flight, timeout_s=args.timeout)
    stopped = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stopped.update(flag=True))
    stats: list[IntervalStats] = []
    carry = 0.0
    try:
        for second, rate in enumerate(profile):
            if stopped["flag"]:
                break
            s, carry = driver.run_second(second, float(rate), carry)
            stats.append(s)
            if second % 10 == 0 and second >= 5:
                prev = stats[second - 5].summary()
                print(f"t={second:4d}s target={rate:6.1f} req/s | ok={prev['ok']:4d} err={prev['errors']:3d} "
                      f"drop={prev['dropped']:3d} p95={prev['p95_latency_ms']:7.1f} ms")
    finally:
        driver.close()
    rows = [s.summary() for s in stats]
    print(f"Done: ok={sum(r['ok'] for r in rows)} errors={sum(r['errors'] for r in rows)} "
          f"dropped={sum(r['dropped'] for r in rows)}  ({time.strftime('%H:%M:%S')})")
    if args.output and rows:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
