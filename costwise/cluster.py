"""Read-only view of the marketplace pods and namespace events for the dashboard.

Uses the same least-privilege ServiceAccount as the controller; the Role only
grants ``get/list/watch`` on pods and events in the ``costwise`` namespace, so
this module cannot change anything. Nothing here is exposed to the browser
except the derived, display-ready summaries.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from autoscaler.kubernetes_client import call_with_retries, load_kube_config

logger = logging.getLogger(__name__)

POD_STATES = ("running", "starting", "terminating", "pending", "failed")


@dataclass(frozen=True)
class PodInfo:
    name: str
    state: str  # running | starting | terminating | pending | failed
    label: str
    ready: bool
    restarts: int
    age_seconds: float | None
    node: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClusterView(Protocol):
    backend: str

    def pods(self) -> list[PodInfo]: ...

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]: ...


def _age(ts: Any, now: float) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return max(0.0, now - dt.timestamp())
    return None


def classify_pod(phase: str | None, deleting: bool, ready: bool, waiting_reason: str | None) -> tuple[str, str]:
    """Map Kubernetes pod status to (state, plain-English label)."""
    if deleting:
        return "terminating", "Stopping"
    if phase == "Running" and ready:
        return "running", "Running"
    if phase in ("Failed",) or waiting_reason in ("CrashLoopBackOff", "ErrImagePull", "ImagePullBackOff",
                                                     "CreateContainerConfigError", "ErrImageNeverPull"):
        return "failed", f"Problem: {waiting_reason or phase}"
    if phase == "Running":
        return "starting", "Starting (not ready yet)"
    if phase == "Pending":
        return ("starting", "Starting") if waiting_reason == "ContainerCreating" else ("pending", "Waiting to start")
    return "pending", phase or "Unknown"


class KubernetesClusterView:
    """Pods and events through the official client (lazy connection)."""

    backend = "kubernetes"

    def __init__(self, namespace: str, selector: str, kube_context: str | None = None, core_api: Any | None = None
                 ) -> None:
        self.namespace = namespace
        self.selector = selector
        self.kube_context = kube_context
        self._core = core_api

    @property
    def core(self) -> Any:
        if self._core is None:
            load_kube_config(self.kube_context)
            from kubernetes import client  # type: ignore[attr-defined]

            self._core = client.CoreV1Api()
        return self._core

    def pods(self) -> list[PodInfo]:
        result = call_with_retries("list pods", lambda: self.core.list_namespaced_pod(
            self.namespace, label_selector=self.selector, _request_timeout=4), f"pods in {self.namespace}", 0, 0)
        now = time.time()
        out: list[PodInfo] = []
        for pod in result.items:
            status = pod.status
            conditions = {c.type: c.status for c in (status.conditions or [])}
            ready = conditions.get("Ready") == "True"
            restarts = 0
            waiting = None
            for cs in status.container_statuses or []:
                restarts += int(cs.restart_count or 0)
                if cs.state is not None and cs.state.waiting is not None:
                    waiting = cs.state.waiting.reason
            state, label = classify_pod(status.phase, pod.metadata.deletion_timestamp is not None, ready, waiting)
            out.append(PodInfo(pod.metadata.name, state, label, ready, restarts,
                               _age(pod.metadata.creation_timestamp, now), getattr(pod.spec, "node_name", None)))
        order = {s: i for i, s in enumerate(POD_STATES)}
        out.sort(key=lambda p: (order.get(p.state, 9), -(p.age_seconds or 0), p.name))
        return out

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        result = call_with_retries("list events", lambda: self.core.list_namespaced_event(
            self.namespace, _request_timeout=4), f"events in {self.namespace}", 0, 0)
        items = []
        for ev in result.items:
            when = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
            items.append({
                "time": when.timestamp() if isinstance(when, datetime) else None,
                "type": ev.type, "reason": ev.reason, "object": f"{ev.involved_object.kind}/{ev.involved_object.name}",
                "message": (ev.message or "")[:300], "count": ev.count,
            })
        items.sort(key=lambda e: e["time"] or 0)
        return items[-limit:]


class DryRunClusterView:
    """Pods derived from the in-memory dry-run scaler (clearly labelled, no cluster)."""

    backend = "dry-run"

    def __init__(self, scaler: Any) -> None:
        self.scaler = scaler

    def pods(self) -> list[PodInfo]:
        n = self.scaler.get_replicas()
        return [PodInfo(f"dry-run-pod-{i + 1}", "running", "Simulated (dry run)", True, 0, None) for i in range(n)]

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return []
