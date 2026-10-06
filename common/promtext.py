"""Tiny, dependency-free Prometheus metrics registry (text exposition format 0.0.4).

Why not ``prometheus_client``? The marketplace and the traffic generator run as
many small pods on a 4 GB laptop; this module keeps their image and memory
footprint minimal and has no transitive dependencies. It implements exactly what
the project needs:

* :class:`Counter`  (exposed as ``<name>_total``),
* :class:`Gauge`,
* :class:`Histogram` (cumulative ``_bucket`` / ``_sum`` / ``_count``),
* optional label names with bounded cardinality (callers use route templates),
* process metrics ``process_cpu_seconds_total`` and ``process_resident_memory_bytes``.

Thread-safe; :meth:`Registry.render` returns the text served on ``/metrics``.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable, Iterable, Sequence

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_BUCKETS: tuple[float, ...] = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

LabelKey = tuple[str, ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def _label_str(names: Sequence[str], values: Sequence[str], extra: str = "") -> str:
    parts = [f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        if not name.replace("_", "").replace(":", "").isalnum():
            raise ValueError(f"invalid metric name {name!r}")
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> LabelKey:
        if set(labels) != set(self.labelnames):
            raise ValueError(f"{self.name} expects labels {self.labelnames}, got {sorted(labels)}")
        return tuple(str(labels[n]) for n in self.labelnames)

    def header(self, exposed_name: str) -> list[str]:
        return [f"# HELP {exposed_name} {self.documentation}", f"# TYPE {exposed_name} {self.kind}"]

    def render(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError


class _CounterChild:
    def __init__(self, parent: Counter, key: LabelKey) -> None:
        self._parent, self._key = parent, key

    def inc(self, amount: float = 1.0) -> None:
        self._parent._inc(self._key, amount)


class Counter(_Metric):
    """Monotonically increasing counter, exposed as ``<name>_total``."""

    kind = "counter"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name.removesuffix("_total"), documentation, labelnames)
        self._values: dict[LabelKey, float] = {}
        if not self.labelnames:
            self._values[()] = 0.0

    def labels(self, **labels: str) -> _CounterChild:
        key = self._key(labels)
        with self._lock:
            self._values.setdefault(key, 0.0)
        return _CounterChild(self, key)

    def inc(self, amount: float = 1.0) -> None:
        self._inc((), amount)

    def _inc(self, key: LabelKey, amount: float) -> None:
        if amount < 0:
            raise ValueError("counters can only increase")
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        with self._lock:
            return self._values.get(self._key(labels) if labels else (), 0.0)

    def render(self) -> list[str]:
        exposed = f"{self.name}_total"
        lines = self.header(exposed)
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            lines.append(f"{exposed}{_label_str(self.labelnames, key)} {_fmt(value)}")
        return lines


class _GaugeChild:
    def __init__(self, parent: Gauge, key: LabelKey) -> None:
        self._parent, self._key = parent, key

    def set(self, value: float) -> None:
        self._parent._set(self._key, value)

    def inc(self, amount: float = 1.0) -> None:
        self._parent._add(self._key, amount)

    def dec(self, amount: float = 1.0) -> None:
        self._parent._add(self._key, -amount)


class Gauge(_Metric):
    """Value that can go up and down (optionally computed by a callback)."""

    kind = "gauge"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        function: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self._values: dict[LabelKey, float] = {}
        self._function = function
        if not self.labelnames and function is None:
            self._values[()] = 0.0

    def labels(self, **labels: str) -> _GaugeChild:
        key = self._key(labels)
        with self._lock:
            self._values.setdefault(key, 0.0)
        return _GaugeChild(self, key)

    def set(self, value: float) -> None:
        self._set((), value)

    def inc(self, amount: float = 1.0) -> None:
        self._add((), amount)

    def dec(self, amount: float = 1.0) -> None:
        self._add((), -amount)

    def _set(self, key: LabelKey, value: float) -> None:
        with self._lock:
            self._values[key] = float(value)

    def _add(self, key: LabelKey, amount: float) -> None:
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        if self._function is not None:
            return float(self._function())
        with self._lock:
            return self._values.get(self._key(labels) if labels else (), 0.0)

    def clear(self) -> None:
        with self._lock:
            self._values = {} if self.labelnames else {(): 0.0}

    def render(self) -> list[str]:
        lines = self.header(self.name)
        if self._function is not None:
            try:
                value = float(self._function())
            except Exception:  # noqa: BLE001 - a broken callback must not break /metrics
                return lines
            lines.append(f"{self.name} {_fmt(value)}")
            return lines
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            lines.append(f"{self.name}{_label_str(self.labelnames, key)} {_fmt(value)}")
        return lines


class _HistogramChild:
    def __init__(self, parent: Histogram, key: LabelKey) -> None:
        self._parent, self._key = parent, key

    def observe(self, value: float) -> None:
        self._parent._observe(self._key, value)


class Histogram(_Metric):
    """Cumulative histogram with fixed buckets."""

    kind = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self.buckets = tuple(sorted(float(b) for b in buckets))
        if not self.buckets:
            raise ValueError("histogram needs at least one bucket")
        self._data: dict[LabelKey, list[float]] = {}
        if not self.labelnames:
            self._data[()] = self._empty()

    def _empty(self) -> list[float]:
        # bucket counts..., +Inf count, sum
        return [0.0] * (len(self.buckets) + 2)

    def labels(self, **labels: str) -> _HistogramChild:
        key = self._key(labels)
        with self._lock:
            self._data.setdefault(key, self._empty())
        return _HistogramChild(self, key)

    def observe(self, value: float) -> None:
        self._observe((), value)

    def _observe(self, key: LabelKey, value: float) -> None:
        with self._lock:
            data = self._data.setdefault(key, self._empty())
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    data[i] += 1
            data[-2] += 1
            data[-1] += value

    def count(self, **labels: str) -> float:
        with self._lock:
            data = self._data.get(self._key(labels) if labels else ())
            return data[-2] if data else 0.0

    def render(self) -> list[str]:
        lines = self.header(self.name)
        with self._lock:
            items = sorted((k, list(v)) for k, v in self._data.items())
        for key, data in items:
            for bound, value in zip(self.buckets, data[: len(self.buckets)], strict=True):
                le = f'le="{_fmt(bound)}"'
                lines.append(f"{self.name}_bucket{_label_str(self.labelnames, key, le)} {_fmt(value)}")
            inf = 'le="+Inf"'
            lines.append(f"{self.name}_bucket{_label_str(self.labelnames, key, inf)} {_fmt(data[-2])}")
            lines.append(f"{self.name}_sum{_label_str(self.labelnames, key)} {_fmt(data[-1])}")
            lines.append(f"{self.name}_count{_label_str(self.labelnames, key)} {_fmt(data[-2])}")
        return lines


def _process_cpu_seconds() -> float:
    t = os.times()
    return float(t.user + t.system)


def _process_rss_bytes() -> float:
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        return float(pages * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, AttributeError, IndexError):
        pass
    try:  # macOS / BSD
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return float(rss if os.uname().sysname == "Darwin" else rss * 1024)
    except Exception:  # noqa: BLE001 - Windows has neither; report NaN
        return float("nan")


class Registry:
    """A set of metrics rendered together."""

    def __init__(self, process_metrics: bool = True) -> None:
        self._metrics: list[_Metric] = []
        self._names: set[str] = set()
        self._lock = threading.Lock()
        self.started = time.time()
        if process_metrics:
            self.gauge("process_cpu_seconds_total", "Total user and system CPU time in seconds.",
                       function=_process_cpu_seconds).kind = "counter"
            self.gauge("process_resident_memory_bytes", "Resident memory size in bytes.",
                       function=_process_rss_bytes)
            self.gauge("process_start_time_seconds", "Start time of the process since unix epoch.",
                       function=lambda: self.started)

    def _add(self, metric: _Metric) -> None:
        with self._lock:
            if metric.name in self._names:
                raise ValueError(f"duplicate metric {metric.name}")
            self._names.add(metric.name)
            self._metrics.append(metric)

    def counter(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> Counter:
        metric = Counter(name, documentation, labelnames)
        self._add(metric)
        return metric

    def gauge(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        function: Callable[[], float] | None = None,
    ) -> Gauge:
        metric = Gauge(name, documentation, labelnames, function)
        self._add(metric)
        return metric

    def histogram(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> Histogram:
        metric = Histogram(name, documentation, labelnames, buckets)
        self._add(metric)
        return metric

    def render(self) -> str:
        with self._lock:
            metrics = list(self._metrics)
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"


def parse_samples(text: str) -> dict[str, float]:
    """Parse exposition text into ``{"name{labels}": value}`` (used by tests and health checks)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        try:
            out[key] = float(value)
        except ValueError:
            continue
    return out
