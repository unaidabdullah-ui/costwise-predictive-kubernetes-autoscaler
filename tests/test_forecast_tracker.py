import pytest

from autoscaler.forecast_tracker import ForecastTracker


def feed(tracker, forecasts_and_actuals):
    t = 0.0
    for forecast, baseline, actual in forecasts_and_actuals:
        tracker.observe(t, actual)
        tracker.register(t, forecast, baseline=baseline)
        t += 5.0
    return t


def test_errors_mature_at_their_target_time():
    tr = ForecastTracker(5.0)
    tr.register(0.0, [11.0, 12.0])
    m = tr.observe(5.0, 10.0)
    assert [(x.steps_ahead, x.error) for x in m] == [(1, 1.0)]
    m = tr.observe(10.0, 10.0)
    assert [(x.steps_ahead, x.error) for x in m] == [(2, 2.0)]
    assert tr.mae(1) == 1.0


def test_confidence_high_when_model_beats_naive_on_a_ramp():
    tr = ForecastTracker(5.0)
    # load rises 2 req/s every step; the model predicts the rise exactly, naive does not
    rows = [([2.0 * (i + 1)], 2.0 * i, 2.0 * i) for i in range(12)]
    feed(tr, rows)
    assert tr.confidence(min_samples=6, tolerance=14.0) == pytest.approx(1.0)


def test_confidence_low_for_a_badly_wrong_model():
    tr = ForecastTracker(5.0)
    rows = [([60.0], 5.0, 5.0) for _ in range(12)]  # constant 5 req/s, model says 60
    feed(tr, rows)
    conf = tr.confidence(min_samples=6, tolerance=14.0)
    assert conf == 0.0


def test_confidence_needs_enough_samples_and_resets():
    tr = ForecastTracker(5.0)
    feed(tr, [([5.0], 5.0, 5.0)] * 3)
    assert tr.confidence(min_samples=6, tolerance=14.0) is None
    tr.reset()
    assert tr.mae(1) is None
