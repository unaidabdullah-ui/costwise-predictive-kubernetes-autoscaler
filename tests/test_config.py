import pytest

from autoscaler.config import AutoscalerConfig, ConfigError


def test_defaults_are_valid_and_match_the_demo():
    cfg = AutoscalerConfig().validate()
    assert (cfg.min_replicas, cfg.max_replicas) == (1, 8)
    assert cfg.replica_capacity_at_target == pytest.approx(14.0)
    assert cfg.horizon_seconds == 30
    assert cfg.currency_symbol == "₹"


def test_friendly_aliases_from_env():
    env = {"BUDGET_PER_HOUR": "20", "COST_PER_REPLICA_HOUR": "4", "FORECAST_HORIZON": "4",
           "SCALE_UP_COOLDOWN": "15", "SCALE_DOWN_COOLDOWN": "40", "MAX_REPLICAS": "5"}
    cfg = AutoscalerConfig.from_env(env)
    assert cfg.max_budget == 20 and cfg.cost_per_replica == 4 and cfg.prediction_horizon == 4
    assert cfg.scale_up_cooldown_seconds == 15 and cfg.cooldown_seconds == 40 and cfg.max_replicas == 5


@pytest.mark.parametrize("env", [
    {"BUDGET_PER_HOUR": "0"}, {"MIN_REPLICAS": "0"}, {"MIN_REPLICAS": "5", "MAX_REPLICAS": "2"},
    {"TARGET_UTILIZATION": "1.5"}, {"OPTIMIZER_MODE": "magic"}, {"BUDGET_PER_HOUR": "abc"},
])
def test_invalid_configuration_is_rejected(env):
    with pytest.raises(ConfigError):
        AutoscalerConfig.from_env(env)


def test_public_dict_redacts_secrets():
    cfg = AutoscalerConfig(prometheus_bearer_token="s3cret")
    assert "s3cret" not in str(cfg.public_dict())
