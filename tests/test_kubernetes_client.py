from types import SimpleNamespace

import pytest

from autoscaler.kubernetes_client import (
    ClusterConnectionError,
    DeploymentNotFoundError,
    DryRunScaler,
    KubernetesScaler,
    PermissionDeniedError,
    ScalerError,
)


class ApiError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeApps:
    def __init__(self, fail=None):
        self.fail = list(fail or [])
        self.patches = []

    def _maybe_fail(self):
        if self.fail:
            raise self.fail.pop(0)

    def read_namespaced_deployment(self, name, ns):
        self._maybe_fail()
        return SimpleNamespace(spec=SimpleNamespace(replicas=3),
                               status=SimpleNamespace(ready_replicas=2, available_replicas=2, updated_replicas=3,
                                                      unavailable_replicas=1))

    def patch_namespaced_deployment_scale(self, name, ns, body):
        self._maybe_fail()
        self.patches.append((name, ns, body))


def scaler(apps):
    return KubernetesScaler("costwise", "marketplace", apps_api=apps, retries=2, backoff_seconds=0,
                            sleep=lambda s: None)


def test_status_and_scale_use_the_scale_subresource():
    apps = FakeApps()
    s = scaler(apps)
    st = s.get_status()
    assert (st.spec_replicas, st.ready_replicas, st.unavailable_replicas) == (3, 2, 1)
    s.scale(5)
    assert apps.patches == [("marketplace", "costwise", {"spec": {"replicas": 5}})]


def test_transient_errors_are_retried():
    apps = FakeApps(fail=[ApiError(503), ApiError(429)])
    assert scaler(apps).get_status().spec_replicas == 3


@pytest.mark.parametrize("status,exc", [(404, DeploymentNotFoundError), (403, PermissionDeniedError),
                                        (401, PermissionDeniedError)])
def test_permanent_errors_are_classified(status, exc):
    with pytest.raises(exc) as info:
        scaler(FakeApps(fail=[ApiError(status)])).get_status()
    assert info.value.hint


def test_connection_errors_become_cluster_connection_error():
    with pytest.raises(ClusterConnectionError):
        scaler(FakeApps(fail=[ConnectionRefusedError("refused")] * 3)).get_status()


def test_retries_exhausted():
    with pytest.raises(ScalerError):
        scaler(FakeApps(fail=[ApiError(500)] * 3)).scale(2)


def test_dry_run_scaler_is_labelled():
    d = DryRunScaler(2)
    d.scale(4)
    assert d.backend == "dry-run" and d.get_status().ready_replicas == 4
    with pytest.raises(ValueError):
        d.scale(-1)
