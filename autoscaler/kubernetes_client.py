"""Kubernetes integration: read and change ``Deployment.spec.replicas``.

Uses the official Kubernetes Python client. Connection order:

1. in-cluster configuration (ServiceAccount token) when running as a pod,
2. ``$KUBECONFIG`` / ``~/.kube/config``. ``start-demo`` points ``KUBECONFIG`` at a
   kubeconfig holding a short-lived token of the least-privilege
   ``cost-aware-autoscaler`` ServiceAccount, never the Minikube admin user.

Scaling uses the ``deployments/scale`` sub-resource (``PATCH``) - the only write
the RBAC Role allows. Transient errors (429/5xx/connection) are retried with
exponential back-off; 401/403/404 are raised immediately with an actionable
message. ``DryRunScaler`` keeps the count in memory for development without a
cluster and is always labelled as such.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ScalerError(RuntimeError):
    """Base error for replica-scaling backends."""

    hint: str = ""


class ClusterConnectionError(ScalerError):
    hint = "Start Minikube (.\\start-demo.ps1) or check KUBECONFIG."


class DeploymentNotFoundError(ScalerError):
    hint = "The marketplace Deployment is missing: run .\\start-demo.ps1 again."


class PermissionDeniedError(ScalerError):
    hint = ("The service-account token expired or RBAC is missing: run .\\start-demo.ps1 again "
            "(it renews the 12 h token and re-applies kubernetes/rbac.yaml).")


@dataclass(frozen=True)
class DeploymentStatus:
    name: str
    namespace: str
    spec_replicas: int
    ready_replicas: int
    available_replicas: int
    updated_replicas: int = 0
    unavailable_replicas: int = 0

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class ReplicaScaler(Protocol):
    backend: str

    def get_status(self) -> DeploymentStatus: ...

    def get_replicas(self) -> int: ...

    def scale(self, replicas: int) -> None: ...


def load_kube_config(kube_context: str | None = None) -> str:
    """Configure the client; returns ``"in-cluster"`` or ``"kubeconfig"``."""
    try:
        from kubernetes.config.config_exception import ConfigException

        from kubernetes import config  # type: ignore[attr-defined]
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise ClusterConnectionError("the 'kubernetes' Python package is not installed") from exc
    try:
        config.load_incluster_config()
        return "in-cluster"
    except ConfigException:
        try:
            config.load_kube_config(context=kube_context)
            return "kubeconfig"
        except (ConfigException, FileNotFoundError, TypeError) as exc:
            raise ClusterConnectionError(
                "No Kubernetes configuration found (not in a pod and no usable kubeconfig)."
            ) from exc


def call_with_retries(operation: str, fn: Callable[[], T], target: str, retries: int,
                      backoff_seconds: float, sleep: Callable[[float], None] = time.sleep) -> T:
    """Run ``fn`` translating API errors into :class:`ScalerError` subclasses."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classified below
            status = getattr(exc, "status", None)
            if status == 404:
                raise DeploymentNotFoundError(f"{target} not found ({operation})") from exc
            if status in (401, 403):
                kind = "expired or invalid credentials" if status == 401 else "permission denied by RBAC"
                raise PermissionDeniedError(f"Kubernetes rejected {operation} on {target}: {kind}") from exc
            retryable = status is None or status in RETRYABLE_STATUS
            if not retryable or attempt >= retries:
                name = type(exc).__name__
                is_connection = isinstance(exc, (OSError, ConnectionError)) or any(
                    s in name for s in ("Connection", "MaxRetry", "Timeout"))
                if status is None and is_connection:
                    raise ClusterConnectionError(f"Kubernetes API unreachable during {operation}: {exc}") from exc
                raise ScalerError(f"{operation} failed after {attempt + 1} attempt(s): {exc}") from exc
            delay = backoff_seconds * (2**attempt)
            logger.warning("Kubernetes API call failed; retrying",
                           extra={"operation": operation, "attempt": attempt + 1, "delay_s": delay})
            sleep(delay)
            attempt += 1


class KubernetesScaler:
    """Scale a Deployment through the Kubernetes API."""

    backend = "kubernetes"

    def __init__(self, namespace: str, deployment: str, apps_api: Any | None = None, retries: int = 2,
                 backoff_seconds: float = 0.5, kube_context: str | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.namespace = namespace
        self.deployment = deployment
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.kube_context = kube_context or None
        self._sleep = sleep
        self._apps = apps_api
        self.connection_mode = "injected" if apps_api is not None else "unconnected"

    def connect(self) -> None:
        if self._apps is not None:
            return
        self.connection_mode = load_kube_config(self.kube_context)
        from kubernetes import client  # type: ignore[attr-defined]  # (local kubernetes/ folder confuses mypy)

        self._apps = client.AppsV1Api()
        logger.info("Connected to Kubernetes", extra={"mode": self.connection_mode, "namespace": self.namespace})

    @property
    def apps(self) -> Any:
        if self._apps is None:
            self.connect()
        return self._apps

    def _call(self, operation: str, fn: Callable[[], T]) -> T:
        return call_with_retries(operation, fn, f"deployment {self.namespace}/{self.deployment}",
                                 self.retries, self.backoff_seconds, self._sleep)

    def list_deployments(self) -> list[str]:
        result = self._call("list deployments", lambda: self.apps.list_namespaced_deployment(self.namespace))
        return sorted(item.metadata.name for item in result.items)

    def get_status(self) -> DeploymentStatus:
        dep = self._call("read deployment",
                         lambda: self.apps.read_namespaced_deployment(self.deployment, self.namespace))
        st = dep.status
        return DeploymentStatus(
            name=self.deployment,
            namespace=self.namespace,
            spec_replicas=int(dep.spec.replicas or 0),
            ready_replicas=int(getattr(st, "ready_replicas", None) or 0),
            available_replicas=int(getattr(st, "available_replicas", None) or 0),
            updated_replicas=int(getattr(st, "updated_replicas", None) or 0),
            unavailable_replicas=int(getattr(st, "unavailable_replicas", None) or 0),
        )

    def get_replicas(self) -> int:
        return self.get_status().spec_replicas

    def scale(self, replicas: int) -> None:
        """Patch ``spec.replicas`` through the ``deployments/scale`` sub-resource."""
        if replicas < 0:
            raise ValueError("replicas must be >= 0")
        body = {"spec": {"replicas": int(replicas)}}
        self._call("patch deployments/scale",
                   lambda: self.apps.patch_namespaced_deployment_scale(self.deployment, self.namespace, body))
        logger.info("Scaled deployment", extra={"deployment": self.deployment, "replicas": replicas})


class DryRunScaler:
    """In-memory scaler for development without a cluster (always labelled 'dry run')."""

    backend = "dry-run"

    def __init__(self, initial_replicas: int = 1, name: str = "marketplace", namespace: str = "dry-run") -> None:
        self._replicas = initial_replicas
        self.name = name
        self.namespace = namespace

    def get_status(self) -> DeploymentStatus:
        r = self._replicas
        return DeploymentStatus(self.name, self.namespace, r, r, r, r, 0)

    def get_replicas(self) -> int:
        return self._replicas

    def scale(self, replicas: int) -> None:
        if replicas < 0:
            raise ValueError("replicas must be >= 0")
        logger.info("DRY-RUN scale (no cluster change)", extra={"from_replicas": self._replicas, "to": replicas})
        self._replicas = replicas
