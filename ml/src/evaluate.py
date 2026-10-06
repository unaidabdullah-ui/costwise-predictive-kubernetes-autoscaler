"""Evaluate the PyTorch LSTM against baselines (optional pipeline).

    python -m ml.src.evaluate

The NumPy demo model is evaluated by ``python -m ml.src.lite evaluate``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.env import load_dotenv
from common.jsonutil import write_json
from common.logging_utils import configure_logging
from ml.src.config import artifacts_dir_from_env, data_path_from_env, model_dir_from_env
from ml.src.dataset import build_datasets
from ml.src.forecasters import BASELINE_NAMES, build_baseline
from ml.src.metrics import per_horizon_report, regression_report
from ml.src.plots import plot_actual_vs_predicted, plot_horizon_error, plot_model_comparison
from ml.src.predict import WorkloadPredictor
from ml.src.preprocessing import inverse_scale


def _raw_history(predictor: WorkloadPredictor, x_scaled: np.ndarray, col: int) -> np.ndarray:
    fs = predictor.feature_scaler
    lo, hi = fs.data_min_[col], fs.data_max_[col]
    return np.maximum(x_scaled[:, :, col] * (hi - lo) + lo, 0.0)


def evaluate(predictor: WorkloadPredictor, data_path: Path) -> dict[str, Any]:
    from ml.src.model import predict_array

    cfg = predictor.config
    bundle = build_datasets(pd.read_csv(data_path), cfg)
    actual = inverse_scale(predictor.target_scaler, bundle.y_test)
    lstm = np.maximum(inverse_scale(predictor.target_scaler,
                                    predict_array(predictor.model, bundle.x_test, predictor.device)), 0.0)
    results = {"lstm": {"overall": regression_report(actual, lstm), "per_horizon": per_horizon_report(actual, lstm)}}
    hist = _raw_history(predictor, bundle.x_test, cfg.feature_columns.index(cfg.target_column))
    for name in BASELINE_NAMES:
        fc = build_baseline(name, cfg.prediction_horizon, cfg.sequence_length)
        pred = np.stack([fc.predict(h) for h in hist])
        results[name] = {"overall": regression_report(actual, pred), "per_horizon": per_horizon_report(actual, pred)}
    mask = bundle.test_series_ids == bundle.test_series_ids[0]
    return {"results": results, "actual": actual[mask], "pred": lstm[mask]}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Evaluate the PyTorch LSTM vs baselines.")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args(argv)
    configure_logging()
    predictor = WorkloadPredictor.load(args.model_dir or model_dir_from_env())
    data_path = args.data or data_path_from_env()
    artifacts = args.artifacts_dir or artifacts_dir_from_env()
    out = evaluate(predictor, data_path)
    res = out["results"]
    plot_actual_vs_predicted(out["actual"][:, 0], out["pred"][:, 0], artifacts / "torch_actual_vs_predicted.png",
                             "PyTorch LSTM one-step forecast (test series)")
    plot_horizon_error({n: [s["mae"] for s in r["per_horizon"]] for n, r in res.items()},
                       artifacts / "torch_forecast_horizon_error.png")
    plot_model_comparison([{"model": n, "mae": r["overall"]["mae"], "rmse": r["overall"]["rmse"]}
                           for n, r in res.items()], artifacts / "torch_model_comparison.png")
    write_json(artifacts / "torch_evaluation_report.json", {"model": predictor.info(), "results": res})
    for name, r in res.items():
        o = r["overall"]
        print(f"{name:<16}{o['mae']:>10.3f}{o['rmse']:>10.3f}{o['mape']:>10.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
