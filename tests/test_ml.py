import numpy as np
import pytest

from ml.src import lite
from ml.src.forecasters import load_forecaster
from ml.src.metrics import mae, mape, rmse
from ml.src.numpy_lstm import NumpyLSTM, gradient_check


def test_lstm_gradients_match_finite_differences():
    rng = np.random.default_rng(0)
    model = NumpyLSTM(4, 6, 3, seed=1)
    x = rng.normal(size=(5, 7, 4))
    y = rng.normal(size=(5, 3)) * 0.1
    assert gradient_check(model, x, y) < 1e-4


def test_save_and_load_roundtrip(tmp_path):
    model = NumpyLSTM(4, 5, 2, seed=3)
    x = np.random.default_rng(1).normal(size=(2, 4, 4))
    path = tmp_path / "m.npz"
    model.save(path, {"hello": "world"})
    loaded, meta = NumpyLSTM.load(path)
    assert meta["hello"] == "world"
    np.testing.assert_allclose(model.predict(x), loaded.predict(x))


def test_metrics():
    a, p = np.array([10.0, 20.0]), np.array([12.0, 18.0])
    assert mae(a, p) == 2 and rmse(a, p) == 2
    assert mape(a, p) == pytest.approx(15.0)


def test_shipped_model_loads_and_forecasts_a_rise():
    f = load_forecaster("lstm_lite", None, 6, 12)
    info = f.info()
    assert info["horizon"] == 6 and info["step_seconds"] == 5
    assert info["data"]["observed_points"] == 0  # shipped model: bootstrap data only (labelled)
    ramp = np.concatenate([np.full(10, 7.0), np.linspace(8, 30, 8)])
    out = f.predict(ramp)
    assert out.shape == (6,) and np.all(out >= 0)
    assert out.max() > ramp[-1]  # expects the rise to continue


def test_quick_training_writes_a_loadable_labelled_model(tmp_path):
    cfg = lite.LiteConfig()
    cfg.demo_runs, cfg.generic_series, cfg.epochs, cfg.hidden_size = 4, 2, 1, 8
    path = tmp_path / "quick.npz"
    meta = lite.train(cfg, path, history_path=None)
    assert meta["data"]["observed_points"] == 0 and "synthetic" in meta["data"]["bootstrap_label"]
    f = lite.LiteLSTMForecaster.load(path)
    assert f.predict(np.full(20, 5.0)).shape == (6,)


def test_missing_model_is_reported(tmp_path):
    from ml.src.forecasters import ModelNotAvailableError

    with pytest.raises(ModelNotAvailableError):
        lite.LiteLSTMForecaster.load(tmp_path / "nope.npz")


def test_observed_history_is_loaded_in_contiguous_segments(tmp_path):
    csv = tmp_path / "h.csv"
    rows = ["timestamp,request_rate"] + [f"{1000 + 5 * i},{i % 7}" for i in range(60)] + \
        [f"{5000 + 5 * i},{i}" for i in range(10)]
    csv.write_text("\n".join(rows))
    segs = lite.load_observed(csv, step=5.0, min_points=54)
    assert len(segs) == 1 and segs[0].size == 60
