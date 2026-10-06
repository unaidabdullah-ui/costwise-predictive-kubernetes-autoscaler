import pytest

from autoscaler.metrics_source import MetricsUnavailableError, PrometheusMetricsSource


class Resp:
    def __init__(self, payload, ok=True):
        self.payload, self.ok = payload, ok

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def __init__(self, answers):
        self.answers = answers
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        q = (params or {}).get("query", "")
        for key, payload in self.answers.items():
            if key in q:
                return Resp(payload)
        return Resp({"status": "success", "data": {"resultType": "vector", "result": []}})


def vec(v):
    return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, str(v)]}]}}


def mat(vals):
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {}, "values": [[100 + 5 * i, str(v)] for i, v in enumerate(vals)]}]}}


def src(answers):
    return PrometheusMetricsSource("http://p", "WORKLOAD", "LAT", "CPU", "MEM", session=Session(answers),
                                   clock=lambda: 100 + 5 * 11, targets_up_query="UP")


def test_snapshot_reads_load_history_and_extras():
    s = src({"WORKLOAD": vec(12.5), "LAT": vec(80), "CPU": vec(0.3), "MEM": vec(1e8)})
    s.session.answers["WORKLOAD"] = vec(12.5)
    snap = s.snapshot(12)
    assert snap.request_rate == 12.5 and snap.latency_p95_ms == 80


def test_empty_workload_with_scraped_targets_means_zero_load():
    snap = src({"UP": vec(2)}).snapshot(12)
    assert snap.request_rate == 0.0


def test_empty_workload_without_targets_is_unavailable():
    with pytest.raises(MetricsUnavailableError):
        src({}).snapshot(12)


def test_prometheus_error_payload():
    with pytest.raises(MetricsUnavailableError):
        src({"WORKLOAD": {"status": "error", "error": "bad query"}}).snapshot(12)
