from common.promtext import Registry, parse_samples


def test_render_counter_gauge_histogram():
    r = Registry(process_metrics=False)
    c = r.counter("demo_requests", "Requests.", ("route",))
    c.labels(route="/a").inc()
    c.labels(route="/a").inc(2)
    g = r.gauge("demo_value", "Value.", function=lambda: 4.5)
    h = r.histogram("demo_seconds", "Latency.", buckets=(0.1, 1.0))
    h.observe(0.05)
    h.observe(0.5)
    text = r.render()
    s = parse_samples(text)
    assert s['demo_requests_total{route="/a"}'] == 3
    assert s["demo_value"] == 4.5
    assert s['demo_seconds_bucket{le="0.1"}'] == 1 and s['demo_seconds_bucket{le="+Inf"}'] == 2
    assert s["demo_seconds_count"] == 2
    assert "# TYPE demo_requests_total counter" in text
    assert g is not None


def test_process_metrics_present():
    text = Registry().render()
    assert "process_cpu_seconds_total" in text and "process_resident_memory_bytes" in text


def test_label_escaping_and_validation():
    r = Registry(process_metrics=False)
    c = r.counter("x", "X.", ("l",))
    c.labels(l='a"b\\c').inc()
    assert 'l="a\\"b\\\\c"' in r.render()
    try:
        c.labels(other="1")
    except ValueError:
        pass
    else:
        raise AssertionError("wrong labels accepted")
