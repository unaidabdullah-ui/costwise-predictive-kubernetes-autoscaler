"""Simple analytical performance model of one application pod.

Used by the synthetic data generator (to derive CPU / memory / latency columns
from a request-rate series) and by the offline cluster simulator (to estimate
latency and SLA violations for a given number of ready replicas):

* utilisation ``rho = load / (ready_replicas * capacity_per_pod)``
* latency ``base / (1 - rho)`` (capped at rho = 0.95) plus a queueing term while
  the system is overloaded.
"""

from __future__ import annotations

import numpy as np

MAX_STABLE_RHO = 0.95


def utilisation(
    load: np.ndarray | float, replicas: np.ndarray | float, capacity_per_pod: float
) -> np.ndarray:
    """Return ``rho`` (can exceed 1.0 when overloaded)."""
    cap = np.maximum(np.asarray(replicas, dtype=float) * capacity_per_pod, 1e-9)
    return np.asarray(load, dtype=float) / cap


def latency_ms(
    rho: np.ndarray | float, base_latency_ms: float = 25.0, backlog_s: np.ndarray | float = 0.0
) -> np.ndarray:
    """Approximate mean request latency in milliseconds."""
    rho_arr = np.clip(np.asarray(rho, dtype=float), 0.0, MAX_STABLE_RHO)
    return base_latency_ms / (1.0 - rho_arr) + 1000.0 * np.asarray(backlog_s, dtype=float)


def cpu_percent(rho: np.ndarray | float, idle_percent: float = 3.0) -> np.ndarray:
    """CPU utilisation (% of the pod limit) implied by ``rho``; saturates at 100 %."""
    return np.clip(idle_percent + 100.0 * np.asarray(rho, dtype=float), 0.0, 100.0)


def memory_percent(rho: np.ndarray | float, baseline_percent: float = 30.0) -> np.ndarray:
    """Memory utilisation (%): a baseline plus a working-set term growing with load."""
    return np.clip(
        baseline_percent + 25.0 * np.minimum(np.asarray(rho, dtype=float), 1.5), 0.0, 100.0
    )
