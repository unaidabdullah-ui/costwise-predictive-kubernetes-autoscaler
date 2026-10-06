"""Centralised, validated configuration of the autoscaling engine.

Every value can be set with an environment variable (``.env`` / ``.env.example``;
in Kubernetes the optional in-cluster ConfigMap). Friendly names are accepted as
aliases of the original ones, e.g. ``BUDGET_PER_HOUR`` = ``MAX_BUDGET`` and
``COST_PER_REPLICA_HOUR`` = ``COST_PER_REPLICA``. Configuration is validated once
at start-up so that an invalid budget or replica range is rejected immediately
instead of producing a dangerous decision later.

Defaults are tuned for the laptop demo: one marketplace pod (250m CPU) serves
~20 req/s; decisions every 5 s; the LSTM looks 30 s ahead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from common.env import ConfigError, get_choice, get_float, get_int, get_str

DEFAULT_WORKLOAD_QUERY = 'sum(rate(marketplace_http_requests_total{route=~"/api/.*"}[30s]))'
DEFAULT_LATENCY_QUERY = (
    "1000 * histogram_quantile(0.95, sum by (le) "
    '(rate(marketplace_http_request_duration_seconds_bucket{route=~"/api/.*"}[1m])))'
)
DEFAULT_CPU_QUERY = 'sum(rate(process_cpu_seconds_total{job="marketplace"}[1m]))'
DEFAULT_MEMORY_QUERY = 'sum(process_resident_memory_bytes{job="marketplace"})'

SCALER_BACKENDS = ("kubernetes", "dry-run")
CONTROLLER_MODES = ("active", "observe")
OPTIMIZER_MODES = ("cost_aware", "predictive")
FORECASTERS = ("lstm_lite", "lstm", "holt", "naive", "moving_average")
COST_MODELS = ("flat", "resource")
CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


@dataclass(frozen=True)
class AutoscalerConfig:
    """All tunables of the predictive, cost-aware autoscaler."""

    # --- data sources ---------------------------------------------------------------
    prometheus_url: str = "http://127.0.0.1:9090"
    prometheus_timeout_seconds: float = 4.0
    prometheus_bearer_token: str = ""
    workload_query: str = DEFAULT_WORKLOAD_QUERY
    latency_query: str = DEFAULT_LATENCY_QUERY
    cpu_query: str = DEFAULT_CPU_QUERY
    memory_query: str = DEFAULT_MEMORY_QUERY
    # --- Kubernetes target ----------------------------------------------------------------
    kubernetes_namespace: str = "costwise"
    target_deployment: str = "marketplace"
    kube_context: str = ""
    scaler_backend: str = "kubernetes"
    controller_mode: str = "active"
    kubernetes_retries: int = 2
    kubernetes_retry_backoff_seconds: float = 0.5
    # --- forecasting -------------------------------------------------------------------------
    forecaster: str = "lstm_lite"
    model_path: str = ""
    prediction_horizon: int = 6
    sequence_length: int = 12
    default_uncertainty_fraction: float = 0.10
    min_forecast_confidence: float = 0.40
    confidence_min_samples: int = 6
    # --- capacity planning ---------------------------------------------------------------------
    min_replicas: int = 1
    max_replicas: int = 8
    capacity_per_pod: float = 20.0
    target_utilization: float = 0.70
    safety_margin: float = 0.15
    # --- cost optimisation -----------------------------------------------------------------------
    optimizer_mode: str = "cost_aware"
    cost_model: str = "flat"
    cost_per_replica: float = 5.0
    max_budget: float = 35.0
    currency: str = "INR"
    sla_penalty_per_rps: float = 2.0
    change_penalty: float = 0.2
    vcpu_hour_price: float = 16.0
    gb_hour_price: float = 2.0
    pod_cpu_request_cores: float = 0.25
    pod_memory_request_gb: float = 0.125
    # --- control loop / stability -----------------------------------------------------------------
    scaling_interval: float = 5.0
    cooldown_seconds: float = 20.0
    scale_up_cooldown_seconds: float = 10.0
    stabilization_window_seconds: float = 60.0
    min_replica_change: int = 1
    max_scale_up_step: int = 4
    max_scale_down_step: int = 3
    # --- observability ----------------------------------------------------------------------------
    metrics_port: int = 0
    history_csv: str = ""
    log_level: str = "INFO"
    log_format: str = "text"

    # ------------------------------------------------------------------------------------------
    def validate(self) -> AutoscalerConfig:
        """Check invariants; raise :class:`ConfigError` describing every problem."""
        errors: list[str] = []
        if self.min_replicas < 1:
            errors.append("MIN_REPLICAS must be >= 1")
        if self.max_replicas < self.min_replicas:
            errors.append("MAX_REPLICAS must be >= MIN_REPLICAS")
        if self.max_replicas > 100:
            errors.append("MAX_REPLICAS must be <= 100 for this demo")
        if self.capacity_per_pod <= 0:
            errors.append("CAPACITY_PER_POD must be > 0")
        if not 0 < self.target_utilization <= 1:
            errors.append("TARGET_UTILIZATION must be in (0, 1]")
        if not 0 <= self.safety_margin <= 5:
            errors.append("SAFETY_MARGIN must be in [0, 5]")
        if self.cost_per_replica < 0:
            errors.append("COST_PER_REPLICA_HOUR must be >= 0")
        if not self.max_budget > 0:
            errors.append("BUDGET_PER_HOUR must be > 0")
        if self.sla_penalty_per_rps < 0 or self.change_penalty < 0:
            errors.append("SLA_PENALTY_PER_RPS and CHANGE_PENALTY must be >= 0")
        if self.prediction_horizon < 1 or self.sequence_length < 2:
            errors.append("FORECAST_HORIZON must be >= 1 and SEQUENCE_LENGTH >= 2")
        if self.scaling_interval <= 0:
            errors.append("SCALING_INTERVAL must be > 0")
        if min(self.cooldown_seconds, self.scale_up_cooldown_seconds, self.stabilization_window_seconds) < 0:
            errors.append("cooldown / stabilization values must be >= 0")
        if self.min_replica_change < 1:
            errors.append("MIN_REPLICA_CHANGE must be >= 1")
        if self.max_scale_up_step < 1 or self.max_scale_down_step < 1:
            errors.append("MAX_SCALE_UP_STEP and MAX_SCALE_DOWN_STEP must be >= 1")
        if self.kubernetes_retries < 0:
            errors.append("KUBERNETES_RETRIES must be >= 0")
        if not 0 <= self.default_uncertainty_fraction <= 1:
            errors.append("DEFAULT_UNCERTAINTY_FRACTION must be in [0, 1]")
        if not 0 <= self.min_forecast_confidence < 1:
            errors.append("MIN_FORECAST_CONFIDENCE must be in [0, 1)")
        for name, value, allowed in (
            ("SCALER_BACKEND", self.scaler_backend, SCALER_BACKENDS),
            ("CONTROLLER_MODE", self.controller_mode, CONTROLLER_MODES),
            ("OPTIMIZER_MODE", self.optimizer_mode, OPTIMIZER_MODES),
            ("FORECASTER", self.forecaster, FORECASTERS),
            ("COST_MODEL", self.cost_model, COST_MODELS),
        ):
            if value not in allowed:
                errors.append(f"{name} must be one of {allowed}")
        if errors:
            raise ConfigError("Invalid autoscaler configuration: " + "; ".join(errors))
        return self

    @property
    def replica_capacity_at_target(self) -> float:
        """Requests/second one replica should serve at the target utilisation."""
        return self.capacity_per_pod * self.target_utilization

    @property
    def currency_symbol(self) -> str:
        return CURRENCY_SYMBOLS.get(self.currency.upper(), self.currency.upper() + " ")

    @property
    def horizon_seconds(self) -> float:
        return self.prediction_horizon * self.scaling_interval

    def with_overrides(self, **changes: Any) -> AutoscalerConfig:
        """A validated copy with some fields replaced."""
        return replace(self, **changes).validate()

    def public_dict(self) -> dict[str, Any]:
        """Configuration safe to show (secrets redacted)."""
        data = asdict(self)
        if data.get("prometheus_bearer_token"):
            data["prometheus_bearer_token"] = "***redacted***"  # noqa: S105 - placeholder, not a secret
        return data

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AutoscalerConfig:
        """Build and validate the configuration from environment variables."""
        d = cls()
        cfg = cls(
            prometheus_url=get_str("PROMETHEUS_URL", d.prometheus_url, env).rstrip("/"),
            prometheus_timeout_seconds=get_float("PROMETHEUS_TIMEOUT_SECONDS", d.prometheus_timeout_seconds, env),
            prometheus_bearer_token=get_str("PROMETHEUS_BEARER_TOKEN", "", env),
            workload_query=get_str("WORKLOAD_QUERY", d.workload_query, env),
            latency_query=get_str("LATENCY_QUERY", d.latency_query, env),
            cpu_query=get_str("CPU_QUERY", d.cpu_query, env),
            memory_query=get_str("MEMORY_QUERY", d.memory_query, env),
            kubernetes_namespace=get_str("KUBERNETES_NAMESPACE", d.kubernetes_namespace, env),
            target_deployment=get_str("TARGET_DEPLOYMENT", d.target_deployment, env),
            kube_context=get_str("KUBE_CONTEXT", "", env),
            scaler_backend=get_choice("SCALER_BACKEND", d.scaler_backend, SCALER_BACKENDS, env),
            controller_mode=get_choice("CONTROLLER_MODE", d.controller_mode, CONTROLLER_MODES, env),
            kubernetes_retries=get_int("KUBERNETES_RETRIES", d.kubernetes_retries, env),
            kubernetes_retry_backoff_seconds=get_float(
                "KUBERNETES_RETRY_BACKOFF_SECONDS", d.kubernetes_retry_backoff_seconds, env
            ),
            forecaster=get_choice("FORECASTER", d.forecaster, FORECASTERS, env),
            model_path=get_str(("MODEL_PATH", "MODEL_DIR"), "", env),
            prediction_horizon=get_int(("FORECAST_HORIZON", "PREDICTION_HORIZON"), d.prediction_horizon, env),
            sequence_length=get_int("SEQUENCE_LENGTH", d.sequence_length, env),
            default_uncertainty_fraction=get_float(
                "DEFAULT_UNCERTAINTY_FRACTION", d.default_uncertainty_fraction, env
            ),
            min_forecast_confidence=get_float("MIN_FORECAST_CONFIDENCE", d.min_forecast_confidence, env),
            confidence_min_samples=get_int("CONFIDENCE_MIN_SAMPLES", d.confidence_min_samples, env),
            min_replicas=get_int("MIN_REPLICAS", d.min_replicas, env),
            max_replicas=get_int("MAX_REPLICAS", d.max_replicas, env),
            capacity_per_pod=get_float("CAPACITY_PER_POD", d.capacity_per_pod, env),
            target_utilization=get_float("TARGET_UTILIZATION", d.target_utilization, env),
            safety_margin=get_float("SAFETY_MARGIN", d.safety_margin, env),
            optimizer_mode=get_choice("OPTIMIZER_MODE", d.optimizer_mode, OPTIMIZER_MODES, env),
            cost_model=get_choice("COST_MODEL", d.cost_model, COST_MODELS, env),
            cost_per_replica=get_float(("COST_PER_REPLICA_HOUR", "COST_PER_REPLICA"), d.cost_per_replica, env),
            max_budget=get_float(("BUDGET_PER_HOUR", "MAX_BUDGET"), d.max_budget, env),
            currency=get_str("CURRENCY", d.currency, env).upper(),
            sla_penalty_per_rps=get_float("SLA_PENALTY_PER_RPS", d.sla_penalty_per_rps, env),
            change_penalty=get_float("CHANGE_PENALTY", d.change_penalty, env),
            vcpu_hour_price=get_float("VCPU_HOUR_PRICE", d.vcpu_hour_price, env),
            gb_hour_price=get_float("GB_HOUR_PRICE", d.gb_hour_price, env),
            pod_cpu_request_cores=get_float("POD_CPU_REQUEST_CORES", d.pod_cpu_request_cores, env),
            pod_memory_request_gb=get_float("POD_MEMORY_REQUEST_GB", d.pod_memory_request_gb, env),
            scaling_interval=get_float("SCALING_INTERVAL", d.scaling_interval, env),
            cooldown_seconds=get_float(("SCALE_DOWN_COOLDOWN", "COOLDOWN_SECONDS"), d.cooldown_seconds, env),
            scale_up_cooldown_seconds=get_float(
                ("SCALE_UP_COOLDOWN", "SCALE_UP_COOLDOWN_SECONDS"), d.scale_up_cooldown_seconds, env
            ),
            stabilization_window_seconds=get_float(
                "STABILIZATION_WINDOW_SECONDS", d.stabilization_window_seconds, env
            ),
            min_replica_change=get_int("MIN_REPLICA_CHANGE", d.min_replica_change, env),
            max_scale_up_step=get_int("MAX_SCALE_UP_STEP", d.max_scale_up_step, env),
            max_scale_down_step=get_int("MAX_SCALE_DOWN_STEP", d.max_scale_down_step, env),
            metrics_port=get_int("METRICS_PORT", d.metrics_port, env),
            history_csv=get_str("HISTORY_CSV", "", env),
            log_level=get_str("LOG_LEVEL", d.log_level, env).upper(),
            log_format=get_choice("LOG_FORMAT", d.log_format, ("text", "json"), env),
        )
        return cfg.validate()


__all__ = ["AutoscalerConfig", "ConfigError"]
