"""Reactive vs predictive, measured from this session's own decisions.

For every scale-up CostWise *applied*, we look at what happened next in the
measured data:

* **lead time** - seconds between the scale-up and the moment the measured load
  first exceeded what the *old* pod count can serve at the target utilisation.
  That moment is the earliest a purely reactive autoscaler (such as the HPA,
  which only sees load that already arrived) could have started reacting.
* **pod start-up** - seconds until Kubernetes reported the new pods ready
  (measured from the Deployment status).

If the lead time is at least the start-up time, the new pods were ready before
the extra load arrived; a reactive autoscaler would at best have started them
when the load arrived, leaving shoppers on overloaded pods for about the
start-up time (a lower bound - real HPAs also wait for their sync period and
metrics window). These numbers are computed only from measurements; nothing is
assumed or invented. When no scale-up has happened yet, the card says so.
"""

from __future__ import annotations

from typing import Any

from autoscaler.state import DecisionRecord

SEARCH_WINDOW_SECONDS = 90.0


def scale_up_episodes(records: list[DecisionRecord], per_pod: float) -> list[dict[str, Any]]:
    episodes = []
    for i, r in enumerate(records):
        if not (r.applied and r.target_replicas is not None and r.current_replicas is not None
                and r.target_replicas > r.current_replicas):
            continue
        old_capacity = r.current_replicas * per_pod
        crossed_at = None
        ready_at = None
        later = records[i + 1:]
        for nxt in later:
            if crossed_at is None and nxt.observed_load is not None and nxt.observed_load > old_capacity:
                crossed_at = nxt.timestamp
            if ready_at is None and (nxt.ready_replicas or 0) >= r.target_replicas:
                ready_at = nxt.timestamp
            if nxt.timestamp - r.timestamp > SEARCH_WINDOW_SECONDS or (crossed_at and ready_at):
                break
        already_over = r.observed_load is not None and r.observed_load > old_capacity
        episodes.append({
            "time": r.timestamp, "from": r.current_replicas, "to": r.target_replicas,
            "load_at_decision": r.observed_load, "old_capacity": old_capacity,
            "forecast_peak": r.forecast_peak, "safe_mode": r.fallback,
            "already_over_capacity": already_over,
            "lead_seconds": None if crossed_at is None or already_over else crossed_at - r.timestamp,
            "load_exceeded_old_capacity": crossed_at is not None or already_over,
            "startup_seconds": None if ready_at is None else ready_at - r.timestamp,
        })
    return episodes


def comparison(records: list[DecisionRecord], per_pod: float | None) -> dict[str, Any]:
    if not per_pod:
        return {"available": False, "message": "Waiting for the first decision."}
    episodes = scale_up_episodes(records, per_pod)
    if not episodes:
        return {"available": False,
                "message": "No scale-up yet in this session. Press FLASH SALE to see the predictive head start."}
    predictive = [e for e in episodes if e["lead_seconds"] is not None]
    latest = episodes[-1]
    startups = [e["startup_seconds"] for e in episodes if e["startup_seconds"] is not None]
    result: dict[str, Any] = {
        "available": True,
        "scale_ups": len(episodes),
        "ahead_of_load": len(predictive),
        "avg_lead_seconds": sum(e["lead_seconds"] for e in predictive) / len(predictive) if predictive else None,
        "avg_startup_seconds": sum(startups) / len(startups) if startups else None,
        "latest": latest,
        "method": "measured in this session (see docs/methodology.md)",
    }
    if latest["lead_seconds"] is not None:
        result["message"] = (f"Last scale-up ({latest['from']} → {latest['to']} pods) happened "
                             f"{latest['lead_seconds']:.0f} s before the load exceeded what {latest['from']} "
                             f"pod(s) can serve. A reactive autoscaler can only start reacting at that moment.")
    elif latest["already_over_capacity"]:
        result["message"] = (f"Last scale-up ({latest['from']} → {latest['to']} pods) was a reaction: the load "
                             f"had already exceeded the old capacity (e.g. a sudden burst the model could not "
                             f"anticipate, or safe mode).")
    else:
        result["message"] = (f"Last scale-up ({latest['from']} → {latest['to']} pods): the load has not exceeded "
                             f"the old capacity (yet) - the extra pods are a precaution.")
    return result
