"""Settings of the CostWise control plane (dashboard + controller), from the environment.

The autoscaling engine itself is configured by :class:`autoscaler.config.AutoscalerConfig`;
this module adds what the dashboard needs: where it listens, where the
traffic generator and the marketplace are, and where local state lives.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from common.env import get_float, get_int, get_str

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_secret_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass
class DashboardSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    state_dir: Path = PROJECT_ROOT / ".costwise"
    generator_url: str = "http://127.0.0.1:8089"
    generator_token: str = field(default="", repr=False)
    marketplace_url: str = "http://127.0.0.1:8080"
    prometheus_public_url: str = "http://127.0.0.1:9090"
    grafana_url: str = ""
    refresh_seconds: float = 2.0
    pod_selector: str = "app=marketplace"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]", "::1")
    series_points: int = 360

    @property
    def history_csv(self) -> Path:
        return self.state_dir / "history.csv"

    @classmethod
    def from_env(cls) -> DashboardSettings:
        d = cls()
        state_dir = Path(get_str("COSTWISE_STATE_DIR", str(d.state_dir)))
        if not state_dir.is_absolute():
            state_dir = PROJECT_ROOT / state_dir
        token = get_str("CONTROL_TOKEN", "") or _read_secret_file(state_dir / "control-token")
        extra_hosts = tuple(h.strip() for h in get_str("DASHBOARD_ALLOWED_HOSTS", "").split(",") if h.strip())
        return cls(
            host=get_str("DASHBOARD_HOST", d.host),
            port=get_int(("DASHBOARD_PORT", "API_PORT"), d.port),
            state_dir=state_dir,
            generator_url=get_str("GENERATOR_URL", d.generator_url).rstrip("/"),
            generator_token=token,
            marketplace_url=get_str("MARKETPLACE_URL", d.marketplace_url).rstrip("/"),
            prometheus_public_url=get_str("PROMETHEUS_PUBLIC_URL", get_str("PROMETHEUS_URL", d.prometheus_public_url)),
            grafana_url=get_str("GRAFANA_URL", ""),
            refresh_seconds=max(1.0, min(3.0, get_float("DASHBOARD_REFRESH_SECONDS", d.refresh_seconds))),
            pod_selector=get_str("POD_SELECTOR", d.pod_selector),
            allowed_hosts=d.allowed_hosts + extra_hosts,
        )

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.state_dir.chmod(0o700)
            except OSError:
                pass
