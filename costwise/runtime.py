"""The CostWise runtime: control loop + data collection + demo actions.

Two background threads:

* **controller** - the predictive, cost-aware loop (:mod:`autoscaler.controller`),
  every ``SCALING_INTERVAL`` seconds (5 s);
* **poller** - every ``DASHBOARD_REFRESH_SECONDS`` (2 s) collects what the
  dashboard shows between decisions: Deployment status, pod list, traffic
  generator status, error ratio, component health.

The web server (:mod:`costwise.server`) only reads :meth:`Runtime.dashboard` and
calls the fixed demo actions below; it never gets a Kubernetes client.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests

from autoscaler.config import AutoscalerConfig
from autoscaler.controller import AutoscalerController, build_controller, build_metrics_source, build_scaler
from autoscaler.events import EventLog
from autoscaler.kubernetes_client import DryRunScaler, ReplicaScaler, ScalerError
from autoscaler.metrics import PrometheusPublisher
from autoscaler.metrics_source import MetricsUnavailableError, PrometheusMetricsSource
from autoscaler.predictor_client import build_local_predictor
from autoscaler.state import ControllerState, DecisionRecord
from costwise.cluster import ClusterView, DryRunClusterView, KubernetesClusterView
from costwise.explain import explain
from costwise.insights import comparison
from costwise.loadgen import GeneratorClient, GeneratorError
from costwise.model_manager import ModelManager
from costwise.settings import DashboardSettings
from workload_generator.plans import PLANS

logger = logging.getLogger(__name__)

ERROR_RATIO_QUERY = (
    '(sum(rate(marketplace_http_requests_total{route=~"/api/.*",status=~"5.."}[1m])) or vector(0))'
    ' / clamp_min(sum(rate(marketplace_http_requests_total{route=~"/api/.*"}[1m])), 0.001)'
)
DEMO_SCENARIOS = ("normal", "busy", "flash_sale", "burst", "recovery", "stopped", "users")
SETTING_TYPES: dict[str, type] = {
    "max_budget": float, "min_replicas": int, "max_replicas": int, "target_utilization": float,
    "safety_margin": float, "cost_per_replica": float, "optimizer_mode": str, "controller_mode": str,
}
STATUS_ORDER = {"good": 0, "unknown": 1, "warning": 2, "critical": 3}


def _comp(status: str, label: str, message: str, hint: str = "") -> dict[str, str]:
    return {"status": status, "label": label, "message": message, "hint": hint}


class Runtime:
    def __init__(self, settings: DashboardSettings, cfg: AutoscalerConfig, *, scaler: ReplicaScaler | None = None,
                 cluster: ClusterView | None = None, metrics_source: PrometheusMetricsSource | None = None,
                 generator: GeneratorClient | None = None, retrain_args: tuple[str, ...] = ()) -> None:
        settings.ensure_state_dir()
        if not cfg.history_csv:  # measured request rates, used by RETRAIN MODEL
            cfg = cfg.with_overrides(history_csv=str(settings.history_csv))
        self.settings = settings
        self.events = EventLog(max_events=400)
        self.publisher = PrometheusPublisher()
        self.state = ControllerState(max_records=settings.series_points)
        self.scaler = scaler or build_scaler(cfg)
        if cluster is not None:
            self.cluster = cluster
        elif isinstance(self.scaler, DryRunScaler):
            self.cluster = DryRunClusterView(self.scaler)
        else:
            self.cluster = KubernetesClusterView(cfg.kubernetes_namespace, settings.pod_selector,
                                                 cfg.kube_context or None)
        self.metrics_source = metrics_source or build_metrics_source(cfg)
        self.predictor = build_local_predictor(cfg.forecaster, Path(cfg.model_path) if cfg.model_path else None,
                                               cfg.prediction_horizon, cfg.sequence_length)
        self.controller: AutoscalerController = build_controller(
            cfg, publisher=self.publisher, state=self.state, predictor=self.predictor, events=self.events,
            scaler=self.scaler, metrics_source=self.metrics_source)
        self.generator = generator or GeneratorClient(settings.generator_url, settings.generator_token)
        model_path = Path(cfg.model_path) if cfg.model_path else None
        self.models = ModelManager(model_path, settings.history_csv, self._reload_model, self._notify_model,
                                   extra_args=retrain_args)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {"generator": None, "generator_error": None, "pods": [], "pods_error": None,
                                       "deployment": None, "deployment_error": None, "error_ratio": None,
                                       "marketplace_ok": None, "prometheus_ok": None, "polled_at": None}
        self._last_scenario: str | None = None
        self._last_health: dict[str, str] = {}
        self.demo_started_at: float | None = None
        self.started_at = time.time()

    # ------------------------------------------------------------------ properties
    @property
    def config(self) -> AutoscalerConfig:
        return self.controller.config

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self.controller.run_forever, args=(self._stop,), name="controller", daemon=True),
            threading.Thread(target=self._poll_forever, name="poller", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self.events.emit("CostWise control plane started", level="success", category="system",
                         data={"mode": self.config.controller_mode, "backend": getattr(self.scaler, "backend", "?")})

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=8)
        self._threads = []

    # ------------------------------------------------------------------ polling
    def _poll_forever(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - the poller must survive anything
                logger.exception("poller iteration failed")
            self._stop.wait(max(0.2, self.settings.refresh_seconds - (time.monotonic() - started)))

    def poll_once(self) -> None:
        update: dict[str, Any] = {"polled_at": time.time()}
        try:
            update["generator"], update["generator_error"] = self.generator.status(), None
        except GeneratorError as exc:
            update["generator"], update["generator_error"] = None, {"message": str(exc), "hint": exc.hint}
        try:
            update["deployment"], update["deployment_error"] = self.scaler.get_status().to_dict(), None
        except ScalerError as exc:
            update["deployment"], update["deployment_error"] = None, {"message": str(exc), "hint": exc.hint}
        try:
            update["pods"], update["pods_error"] = [p.to_dict() for p in self.cluster.pods()], None
        except (ScalerError, Exception) as exc:  # noqa: BLE001 - shown on the dashboard
            update["pods"], update["pods_error"] = [], {"message": str(exc), "hint": getattr(exc, "hint", "")}
        try:
            update["error_ratio"] = self.metrics_source.query_instant(ERROR_RATIO_QUERY)
            update["prometheus_ok"] = True
        except MetricsUnavailableError:
            update["error_ratio"], update["prometheus_ok"] = None, False
        try:
            r = requests.get(f"{self.settings.marketplace_url}/health", timeout=2)
            update["marketplace_ok"] = r.ok
        except requests.RequestException:
            update["marketplace_ok"] = False
        with self._lock:
            self._cache.update(update)
        self._traffic_events(update.get("generator"))
        self._health_events()

    def _traffic_events(self, gen: dict[str, Any] | None) -> None:
        if not gen:
            return
        scenario = gen.get("scenario")
        if self._last_scenario is not None and scenario != self._last_scenario and scenario != "users":
            label = PLANS[scenario].label if scenario in PLANS else scenario
            self.events.emit(f"Traffic scenario is now {label}", category="traffic", data={"scenario": scenario})
        self._last_scenario = scenario

    def _health_events(self) -> None:
        comps = self.health()["components"]
        for name, comp in comps.items():
            prev = self._last_health.get(name)
            if prev is not None and prev != comp["status"]:
                if comp["status"] == "critical":
                    self.events.emit(f"{comp['label']}: {comp['message']}", level="error", category="system")
                elif comp["status"] == "good" and prev == "critical":
                    self.events.emit(f"{comp['label']} recovered", level="success", category="system")
            self._last_health[name] = comp["status"]

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        with self._lock:
            c = dict(self._cache)
        last = self.state.last
        summary = self.state.summary()
        comps: dict[str, dict[str, str]] = {}
        dep = c.get("deployment")
        ready = (dep or {}).get("ready_replicas", 0) if dep else 0
        backend = getattr(self.scaler, "backend", "kubernetes")
        polled = c.get("polled_at") is not None

        if not polled:
            comps["marketplace"] = _comp("unknown", "Marketplace", "Checking ...")
        elif dep and ready >= 1 and c.get("marketplace_ok"):
            comps["marketplace"] = _comp("good", "Marketplace", f"Online - {ready} pod(s) ready")
        elif dep and ready >= 1:
            comps["marketplace"] = _comp("warning", "Marketplace", f"{ready} pod(s) ready, store link not reachable",
                                         "The port-forward to :8080 is restarting; start-demo supervises it.")
        elif dep:
            comps["marketplace"] = _comp("critical", "Marketplace", "No marketplace pod is ready",
                                         "kubectl -n costwise get pods ; see docs/TROUBLESHOOTING.md")
        else:
            comps["marketplace"] = _comp("critical", "Marketplace", "Deployment status unknown",
                                         (c.get("deployment_error") or {}).get("hint", ""))

        if not polled:
            comps["kubernetes"] = _comp("unknown", "Kubernetes", "Checking ...")
        elif c.get("deployment_error"):
            err = c["deployment_error"]
            comps["kubernetes"] = _comp("critical", "Kubernetes", err["message"], err.get("hint", ""))
        elif backend == "dry-run":
            comps["kubernetes"] = _comp("warning", "Kubernetes", "DRY RUN - no cluster is being changed",
                                        "Set SCALER_BACKEND=kubernetes (start-demo does this).")
        else:
            comps["kubernetes"] = _comp("good", "Kubernetes", f"Connected ({backend})")

        if not polled:
            comps["prometheus"] = _comp("unknown", "Prometheus", "Checking ...")
        elif c.get("prometheus_ok") and not (last and any(e.startswith("metrics") for e in last.errors)):
            comps["prometheus"] = _comp("good", "Prometheus", "Collecting marketplace metrics")
        elif c.get("prometheus_ok"):
            comps["prometheus"] = _comp("warning", "Prometheus", "Reachable, but no marketplace data yet",
                                        "Prometheus needs ~30 s after start to scrape the pods.")
        else:
            comps["prometheus"] = _comp("critical", "Prometheus", "Not reachable",
                                        "The port-forward to :9090 or the Prometheus pod is down; run start-demo.")

        info = self.predictor.info()
        if not info.get("available"):
            comps["model"] = _comp("critical", "Forecast model", "Model not loaded - SAFE MODE (reactive)",
                                   "Run: python -m ml.src.lite train   (or press RETRAIN MODEL)")
        elif last and last.fallback:
            reason = last.fallback_reason or "forecast not trusted"
            status = "warning"
            msg = "Warming up - collecting traffic history" if reason.startswith("warming up") else \
                f"Safe mode: {reason}"
            comps["model"] = _comp(status, "Forecast model", msg, "Predictive scaling resumes automatically.")
        else:
            comps["model"] = _comp("good", "Forecast model", f"{info.get('label') or info.get('name')} ready")
        if self.models.running:
            comps["model"]["message"] += " (retraining in background)"

        if c.get("generator_error"):
            err = c["generator_error"]
            comps["traffic"] = _comp("warning", "Traffic generator", err["message"], err.get("hint", ""))
        elif c.get("generator"):
            g = c["generator"]
            comps["traffic"] = _comp("good", "Traffic generator", f"{g.get('label')} - {g.get('active_shoppers', 0):,} "
                                                                  f"shoppers")
        else:
            comps["traffic"] = _comp("unknown", "Traffic generator", "Checking ...")

        stale = (summary["last_iteration_at"] is None
                 or time.time() - summary["last_iteration_at"] > 3 * self.config.scaling_interval + 5)
        if not summary["running"]:
            comps["controller"] = _comp("critical", "Controller", "Not running")
        elif stale and summary["last_iteration_at"] is not None:
            comps["controller"] = _comp("critical", "Controller", "No decision recently (loop stuck?)")
        elif summary["last_iteration_at"] is None:
            comps["controller"] = _comp("unknown", "Controller", "Starting ...")
        else:
            mode = self.config.controller_mode
            comps["controller"] = _comp("good" if mode == "active" else "warning", "Controller",
                                        "Active - applying decisions" if mode == "active"
                                        else "Observe mode - decisions are not applied")
        overall = max((v["status"] for v in comps.values()), key=lambda s: STATUS_ORDER.get(s, 1))
        return {"status": overall, "ok": overall in ("good", "warning"), "components": comps, "time": time.time()}

    # ------------------------------------------------------------------ views
    def _per_pod(self) -> float:
        return self.config.replica_capacity_at_target

    def series(self, records: list[DecisionRecord]) -> dict[str, Any]:
        per_pod = self._per_pod()
        dt = self.config.scaling_interval
        points = []
        forecast_h = []
        for r in records:
            points.append({
                "t": r.timestamp, "actual": r.observed_load, "pods": r.current_replicas, "ready": r.ready_replicas,
                "target": r.target_replicas, "required": r.required_replicas,
                "capacity": None if r.ready_replicas is None else r.ready_replicas * per_pod,
                "safe_mode": r.fallback,
            })
            if r.forecast:
                forecast_h.append({"t": r.timestamp + len(r.forecast) * dt, "v": r.forecast[-1],
                                   "made_at": r.timestamp, "trusted": not r.fallback})
        last = records[-1] if records else None
        future = []
        if last and last.forecast:
            future = [{"t": last.timestamp + (k + 1) * dt, "v": v} for k, v in enumerate(last.forecast)]
            if last.observed_load is not None:
                future.insert(0, {"t": last.timestamp, "v": last.observed_load})
        return {"points": points, "forecast_horizon": forecast_h, "future": future,
                "capacity_per_pod_at_target": per_pod, "interval_seconds": dt,
                "horizon_seconds": dt * (len(last.forecast) if last and last.forecast else self.predictor.horizon)}

    def dashboard(self) -> dict[str, Any]:
        cfg = self.config
        with self._lock:
            c = dict(self._cache)
        records = self.state.history(limit=0)
        last = records[-1] if records else None
        dep = c.get("deployment") or {}
        gen = c.get("generator") or {}
        cost_model = self.controller.cost_model
        spec = dep.get("spec_replicas", last.current_replicas if last else None)
        current_cost = cost_model.hourly_cost(spec) if spec is not None else None
        horizon_s = self.predictor.horizon * cfg.scaling_interval
        info = self.predictor.info()
        decision = None
        if last:
            decision = {k: getattr(last, k) for k in (
                "timestamp", "iteration", "action", "applied", "current_replicas", "ready_replicas",
                "required_replicas", "desired_replicas", "target_replicas", "prediction_next", "forecast_peak",
                "effective_demand", "observed_load", "blocked_by", "fallback", "fallback_reason", "reason",
                "budget_constrained", "capacity_shortfall", "budget_status", "capacity_status",
                "max_affordable_replicas", "errors", "controller_mode", "optimizer_mode", "scaler_backend")}
        return {
            "time": time.time(),
            "refresh_seconds": self.settings.refresh_seconds,
            "health": self.health(),
            "traffic": {
                "available": bool(gen), "scenario": gen.get("scenario"), "label": gen.get("label"),
                "description": gen.get("description"), "shoppers": gen.get("active_shoppers"),
                "target_rps": gen.get("target_rps"), "achieved_rps": gen.get("achieved_rps"),
                "success_ratio": gen.get("success_ratio"), "client_p95_ms": gen.get("latency_p95_ms"),
                "rate_per_shopper": gen.get("rate_per_shopper"), "error": c.get("generator_error"),
                "demo_started_at": self.demo_started_at,
            },
            "metrics": {
                "request_rate": last.observed_load if last else None,
                "latency_p95_ms": last.latency_p95_ms if last else None,
                "error_ratio": c.get("error_ratio"),
                "cpu_cores": last.cpu_cores if last else None,
                "memory_bytes": last.memory_bytes if last else None,
                "source": "Prometheus",
            },
            "kubernetes": {
                "backend": getattr(self.scaler, "backend", "kubernetes"), "namespace": cfg.kubernetes_namespace,
                "deployment": cfg.target_deployment, "desired_replicas": dep.get("spec_replicas"),
                "ready_replicas": dep.get("ready_replicas"), "available_replicas": dep.get("available_replicas"),
                "pods": c.get("pods") or [], "pods_error": c.get("pods_error"),
                "error": c.get("deployment_error"), "min_replicas": cfg.min_replicas, "max_replicas": cfg.max_replicas,
            },
            "forecast": {
                "model": info.get("label") or info.get("name"), "available": bool(info.get("available")),
                "next": last.prediction_next if last else None, "peak": last.forecast_peak if last else None,
                "horizon_seconds": horizon_s, "confidence": last.forecast_confidence if last else None,
                "live_mae": last.forecast_mae if last else None, "live_rmse": last.forecast_rmse if last else None,
                "safe_mode": bool(last and last.fallback), "safe_reason": last.fallback_reason if last else None,
            },
            "decision": decision,
            "cost": {
                "currency": cfg.currency, "symbol": cfg.currency_symbol, "per_pod_hour": cost_model.hourly_cost(1),
                "current_hourly": current_cost, "planned_hourly": last.estimated_future_cost if last else None,
                "budget": cfg.max_budget,
                "utilization": (current_cost / cfg.max_budget) if current_cost is not None else None,
                "status": last.budget_status if last else None,
                "budget_constrained": bool(last and last.budget_constrained),
                "max_affordable_replicas": last.max_affordable_replicas if last else None,
                "accrued_since_reset": last.cumulative_cost if last else 0.0,
                "label": "Estimated cost (simulated prices, not a cloud bill)",
            },
            "why": explain(last, horizon_s),
            "comparison": comparison(records, self._per_pod()),
            "series": self.series(records),
            "events": [e.to_dict() for e in self.events.latest(60)],
            "settings": self.settings_view(),
            "model": {"retrain": dict(self.models.state), "label": info.get("label"),
                      "trained_at": info.get("trained_at")},
            "links": {"store": self.settings.marketplace_url, "prometheus": self.settings.prometheus_public_url,
                      "grafana": self.settings.grafana_url or None},
        }

    def settings_view(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "controller_mode": cfg.controller_mode, "optimizer_mode": cfg.optimizer_mode,
            "min_replicas": cfg.min_replicas, "max_replicas": cfg.max_replicas, "max_budget": cfg.max_budget,
            "cost_per_replica": cfg.cost_per_replica, "target_utilization": cfg.target_utilization,
            "safety_margin": cfg.safety_margin, "capacity_per_pod": cfg.capacity_per_pod,
            "scaling_interval": cfg.scaling_interval, "scale_up_cooldown": cfg.scale_up_cooldown_seconds,
            "scale_down_cooldown": cfg.cooldown_seconds, "stabilization_window": cfg.stabilization_window_seconds,
            "max_scale_up_step": cfg.max_scale_up_step, "max_scale_down_step": cfg.max_scale_down_step,
            "min_forecast_confidence": cfg.min_forecast_confidence, "forecast_horizon": cfg.prediction_horizon,
            "sequence_length": cfg.sequence_length, "currency": cfg.currency,
        }

    def technical(self) -> dict[str, Any]:
        last = self.state.last
        info = self.predictor.info()
        return {
            "config": self.config.public_dict(),
            "inputs": self.controller.inputs_snapshot(),
            "last_decision": last.to_dict() if last else None,
            "model_runtime": {k: v for k, v in info.items() if k != "data"},
            "model": self.models.info(),
            "controller": self.state.summary(),
            "kubernetes_events": self._k8s_events(),
            "security": {
                "kubernetes_identity": "ServiceAccount costwise/cost-aware-autoscaler (short-lived token)"
                if getattr(self.scaler, "backend", "") == "kubernetes" else "none (dry run)",
                "rbac": ["deployments: get, list, watch (namespace costwise)",
                         "deployments/scale: get, patch, update (namespace costwise)",
                         "pods, events: get, list, watch (namespace costwise)"],
                "browser_access": "fixed demo actions only; no Kubernetes API is exposed",
                "control_token_configured": self.generator.has_token,
            },
            "versions": {"costwise": __import__("autoscaler").__version__},
        }

    def _k8s_events(self) -> list[dict[str, Any]]:
        try:
            return self.cluster.recent_events(25)
        except Exception as exc:  # noqa: BLE001 - informative only
            return [{"type": "Warning", "reason": "Unavailable", "message": str(exc)[:200]}]

    # ------------------------------------------------------------------ demo actions
    def _notify_model(self, level: str, message: str) -> None:
        self.events.emit(message, level=level, category="model")

    def _reload_model(self) -> bool:
        ok = self.predictor.reload()
        if ok:
            self.publisher.set_model_info(self.predictor.info())
            self.controller.tracker.reset()  # live error statistics belong to the old model
        return ok

    def set_traffic(self, scenario: str) -> dict[str, Any]:
        if scenario not in DEMO_SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; choose from {list(DEMO_SCENARIOS)}")
        status = self.generator.add_users(300) if scenario == "users" else self.generator.set_scenario(scenario)
        label = PLANS[scenario].label
        msg = {"users": f"GENERATE USERS: 300 more shoppers arriving (from {status.get('active_shoppers', 0):,})",
               "stopped": "Traffic stopped - shoppers leaving",
               "flash_sale": "FLASH SALE announced - a rush of shoppers is coming"}.get(
            scenario, f"Traffic set to {label}: {PLANS[scenario].description}")
        self.events.emit(msg, level="info", category="demo", data={"scenario": scenario})
        self._last_scenario = status.get("scenario")
        with self._lock:
            self._cache["generator"], self._cache["generator_error"] = status, None
        return status

    def start_demo(self) -> dict[str, Any]:
        self.demo_started_at = time.time()
        status = self.set_traffic("normal")
        return {"ok": True, "traffic": status, "message": "Demo started with normal traffic"}

    def stop_traffic(self) -> dict[str, Any]:
        return {"ok": True, "traffic": self.set_traffic("stopped")}

    def reset(self) -> dict[str, Any]:
        notes = []
        try:
            self.generator.set_scenario("stopped")
        except GeneratorError as exc:
            notes.append(str(exc))
        self.controller.reset()
        self.events.clear()
        self.demo_started_at = None
        target = self.config.min_replicas
        try:
            if self.config.controller_mode == "active":
                self.scaler.scale(target)
                notes.append(f"marketplace scaled to {target} pod(s)")
            else:
                notes.append("observe mode: pods left unchanged")
        except ScalerError as exc:
            notes.append(f"could not scale: {exc}")
        self.events.emit("Demo reset - traffic stopped, charts and timeline cleared, "
                         f"marketplace back to {target} pod(s). Measured history for retraining is kept.",
                         level="success", category="demo")
        return {"ok": True, "notes": notes}

    def apply_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(SETTING_TYPES)
        if unknown:
            raise ValueError(f"cannot change {sorted(unknown)}")
        typed = {k: SETTING_TYPES[k](v) for k, v in changes.items()}
        self.controller.apply_overrides(**typed)
        text = ", ".join(f"{k}={v}" for k, v in typed.items())
        self.events.emit(f"Settings changed: {text}", category="demo", data=typed)
        return {"ok": True, "settings": self.settings_view()}

    def retrain(self) -> dict[str, Any]:
        return self.models.retrain()


def build_runtime(settings: DashboardSettings | None = None, cfg: AutoscalerConfig | None = None,
                  **kwargs: Any) -> Runtime:
    return Runtime(settings or DashboardSettings.from_env(), cfg or AutoscalerConfig.from_env(), **kwargs)
