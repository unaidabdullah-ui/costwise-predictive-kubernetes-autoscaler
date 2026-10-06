"""Model information and the RETRAIN MODEL action.

Retraining really retrains: it runs ``python -m ml.src.lite train`` in a
separate process (so the dashboard and the control loop keep running), using
the labelled bootstrap data plus the request rates actually measured by
Prometheus during your demos (``.costwise/history.csv``). The new model is
written atomically and then hot-reloaded; if anything fails, the previous model
stays in use and the error is shown. There is no continuous/online learning.
"""

from __future__ import annotations

import csv
import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ml.src.lite import DEFAULT_MODEL_PATH, PROJECT_ROOT

logger = logging.getLogger(__name__)
MIN_SEGMENT_POINTS = 54  # same as ml.src.lite.load_observed: 4.5 min of contiguous 5 s samples


def count_history_rows(path: Path) -> int:
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return max(0, sum(1 for _ in csv.reader(fh)) - 1)
    except OSError:
        return 0


def read_metadata(model_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(model_path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summarize_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Plain numbers for the dashboard (all read from the model's own metadata)."""
    if not meta:
        return {"available": False}
    cfg = meta.get("config", {})
    tm = meta.get("test_metrics") or {}
    lstm = (tm.get("lstm") or {}).get("overall", {})
    naive = (tm.get("naive") or {}).get("overall", {})
    holt = (tm.get("holt") or {}).get("overall", {})
    data = meta.get("data", {})
    obs = meta.get("observed_test_metrics") or {}
    return {
        "available": True,
        "model": meta.get("model"),
        "trained_at": meta.get("trained_at"),
        "training_seconds": meta.get("training_seconds"),
        "parameters": meta.get("parameters"),
        "sequence_length": cfg.get("sequence_length"),
        "horizon": cfg.get("horizon"),
        "step_seconds": cfg.get("step_seconds"),
        "history_seconds": (cfg.get("sequence_length") or 0) * (cfg.get("step_seconds") or 0),
        "horizon_seconds": (cfg.get("horizon") or 0) * (cfg.get("step_seconds") or 0),
        "hidden_size": cfg.get("hidden_size"),
        "epochs_run": len(meta.get("history") or []),
        "mae": lstm.get("mae"), "rmse": lstm.get("rmse"), "mape": lstm.get("mape"),
        "naive_mae": naive.get("mae"), "naive_rmse": naive.get("rmse"),
        "holt_mae": holt.get("mae"), "holt_rmse": holt.get("rmse"),
        "rmse_improvement_vs_naive_pct": tm.get("rmse_improvement_vs_naive_pct"),
        "per_horizon": [{"step": p.get("step"), "mae": p.get("mae"), "rmse": p.get("rmse")}
                        for p in (tm.get("lstm") or {}).get("per_horizon", [])],
        "bootstrap_points": data.get("bootstrap_points"),
        "bootstrap_label": data.get("bootstrap_label"),
        "observed_points": data.get("observed_points"),
        "observed_series": data.get("observed_series"),
        "observed_label": data.get("observed_label"),
        "train_windows": data.get("train_windows"),
        "validation_windows": data.get("validation_windows"),
        "test_windows": data.get("test_windows"),
        "observed_rmse": ((obs.get("lstm") or {}).get("overall") or {}).get("rmse") if obs else None,
        "loss_history": meta.get("history") or [],
    }


class ModelManager:
    def __init__(self, model_path: Path | None, history_csv: Path, reload: Callable[[], bool],
                 notify: Callable[[str, str], None] | None = None, python: str = sys.executable,
                 extra_args: tuple[str, ...] = ()) -> None:
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.history_csv = history_csv
        self._reload = reload
        self._notify = notify or (lambda level, msg: None)
        self._python = python
        self._extra_args = extra_args
        self._lock = threading.Lock()
        self.state: dict[str, Any] = {"status": "idle"}

    def info(self) -> dict[str, Any]:
        summary = summarize_metadata(read_metadata(self.model_path))
        rows = count_history_rows(self.history_csv)
        summary["observed_rows_available"] = rows
        summary["observed_minutes_available"] = round(rows * 5 / 60, 1)
        summary["retrain"] = dict(self.state)
        summary["model_file"] = self.model_path.name
        summary["continuous_learning"] = False
        return summary

    @property
    def running(self) -> bool:
        return self.state.get("status") == "running"

    def retrain(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return dict(self.state)
            rows = count_history_rows(self.history_csv)
            self.state = {"status": "running", "started_at": time.time(), "observed_rows": rows,
                          "message": f"Training on bootstrap data + {rows} measured samples ..."}
        threading.Thread(target=self._run, name="retrain", daemon=True).start()
        self._notify("info", f"Retraining the LSTM (bootstrap data + {rows} measured samples from Prometheus)")
        return dict(self.state)

    def _run(self) -> None:
        cmd = [self._python, "-m", "ml.src.lite", "train", "--json", "--out", str(self.model_path),
               "--history", str(self.history_csv), *self._extra_args]
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=900,
                                  check=False)
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
                raise RuntimeError(" ".join(tail) or f"exit code {proc.returncode}")
            lines = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
            result = json.loads(lines[-1]) if lines else {}
            if not self._reload():
                raise RuntimeError("the new model could not be loaded; the previous model is still in use")
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            self.state = {"status": "failed", "started_at": started, "finished_at": time.time(),
                          "message": f"Retraining failed: {exc}. The previous model is still in use."}
            self._notify("error", self.state["message"])
            logger.error("retraining failed", extra={"error": str(exc)})
            return
        lstm = ((result.get("test_metrics") or {}).get("lstm") or {}).get("overall", {})
        data = result.get("data", {})
        self.state = {
            "status": "succeeded", "started_at": started, "finished_at": time.time(),
            "seconds": round(time.time() - started, 1), "trained_at": result.get("trained_at"),
            "observed_points_used": data.get("observed_points"), "bootstrap_points": data.get("bootstrap_points"),
            "rmse": lstm.get("rmse"), "mae": lstm.get("mae"),
            "message": (f"Model retrained in {time.time() - started:.0f} s using {data.get('observed_points', 0)} "
                        f"measured samples + {data.get('bootstrap_points', 0)} bootstrap samples; "
                        f"test RMSE {lstm.get('rmse', float('nan')):.2f} req/s. The new model is now in use."),
        }
        if not data.get("observed_points"):
            self.state["message"] += (" (No measured segment was long enough yet - at least 4.5 minutes of "
                                      "continuous traffic is needed - so only bootstrap data was used.)")
        self._notify("success", self.state["message"])
