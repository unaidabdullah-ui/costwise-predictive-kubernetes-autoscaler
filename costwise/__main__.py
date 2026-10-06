"""``python -m costwise`` - start the CostWise control plane (controller + dashboard).

    python -m costwise                 # uses .env and the start-demo environment
    python -m costwise --dry-run       # no cluster: in-memory scaler (clearly labelled)
    python -m costwise --check         # validate configuration and exit
"""

from __future__ import annotations

import argparse
import sys

from autoscaler.config import AutoscalerConfig, ConfigError
from common.env import load_dotenv
from common.logging_utils import configure_logging
from costwise.settings import DashboardSettings


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m costwise", description="CostWise control plane")
    parser.add_argument("--dry-run", action="store_true", help="do not touch Kubernetes (in-memory scaler)")
    parser.add_argument("--observe", action="store_true", help="compute decisions but never apply them")
    parser.add_argument("--port", type=int, help="dashboard port (default 8000)")
    parser.add_argument("--check", action="store_true", help="validate configuration and exit")
    parser.add_argument("--log-file", help="also write logs to this file")
    args = parser.parse_args(argv)
    try:
        cfg = AutoscalerConfig.from_env()
        overrides = {}
        if args.dry_run:
            overrides["scaler_backend"] = "dry-run"
        if args.observe:
            overrides["controller_mode"] = "observe"
        if overrides:
            cfg = cfg.with_overrides(**overrides)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    settings = DashboardSettings.from_env()
    if args.port:
        settings.port = args.port
    configure_logging(cfg.log_level, cfg.log_format, args.log_file)
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: DASHBOARD_HOST is not a loopback address; the dashboard has no login. "
              "Only do this on a trusted network.", file=sys.stderr)
    if args.check:
        print("Configuration OK")
        print({k: v for k, v in cfg.public_dict().items() if "query" not in k})
        return 0

    import uvicorn

    from costwise.runtime import build_runtime
    from costwise.server import create_app

    runtime = build_runtime(settings, cfg)
    print(f"CostWise dashboard: http://{settings.host}:{settings.port}  (Ctrl+C to stop)", flush=True)
    uvicorn.run(create_app(runtime), host=settings.host, port=settings.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
