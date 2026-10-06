"""Traffic-generator service (``python -m workload_generator``), port 8089.

Endpoints:
  GET  /health            liveness
  GET  /metrics           Prometheus metrics (shoppers, target rate, outcomes, latency)
  GET  /control/status    current scenario, shoppers, target vs achieved req/s
  POST /control/scenario  {"scenario": "normal|busy|flash_sale|burst|recovery|stopped|users"}
  POST /control/users     {"add": 300}

The two POST endpoints require the ``X-Control-Token`` header to equal the
``CONTROL_TOKEN`` environment variable (from the Kubernetes Secret
``costwise-secrets``). The token is never logged or returned. If no token is
configured, control is disabled (the generator stays in its initial scenario).
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from common.env import get_float, get_int, get_str
from common.promtext import CONTENT_TYPE
from workload_generator.engine import EngineSettings, TrafficEngine
from workload_generator.plans import DEFAULT_RATE_PER_SHOPPER, PLANS

logger = logging.getLogger("workload_generator")


def settings_from_env() -> EngineSettings:
    return EngineSettings(
        target_url=get_str(("TARGET_URL", "STORE_URL"), "http://marketplace:80"),
        rate_per_shopper=get_float("WORKLOAD_RATE", DEFAULT_RATE_PER_SHOPPER),
        workers=get_int("GENERATOR_WORKERS", 64),
        queue_limit=get_int("GENERATOR_QUEUE_LIMIT", 256),
        timeout_s=get_float("GENERATOR_TIMEOUT_SECONDS", 4.0),
    )


def create_app(engine: TrafficEngine | None = None, token: str | None = None,
               initial_scenario: str | None = None) -> Starlette:
    eng = engine or TrafficEngine(settings_from_env())
    control_token = token if token is not None else get_str("CONTROL_TOKEN", "")
    initial = initial_scenario or get_str("INITIAL_SCENARIO", "stopped")

    def authorised(request: Request) -> bool:
        supplied = request.headers.get("x-control-token", "")
        return bool(control_token) and hmac.compare_digest(supplied.encode(), control_token.encode())

    async def body_of(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 4096:
            raise ValueError("body too large")
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    def denied() -> JSONResponse:
        if not control_token:
            return JSONResponse({"error": "control_disabled", "detail": "CONTROL_TOKEN is not configured."}, 503)
        return JSONResponse({"error": "forbidden", "detail": "Missing or wrong X-Control-Token."}, 403)

    async def scenario(request: Request) -> JSONResponse:
        if not authorised(request):
            return denied()
        try:
            data = await body_of(request)
            name = str(data.get("scenario", ""))
            target = data.get("target")
            return JSONResponse(eng.set_scenario(name, float(target) if target is not None else None))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": "bad_request", "detail": str(exc),
                                 "scenarios": sorted(PLANS)}, 400)

    async def users(request: Request) -> JSONResponse:
        if not authorised(request):
            return denied()
        try:
            data = await body_of(request)
            return JSONResponse(eng.add_users(int(data.get("add", 300))))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": "bad_request", "detail": str(exc)}, 400)

    def status(request: Request) -> JSONResponse:
        return JSONResponse(eng.status(), headers={"Cache-Control": "no-store"})

    def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "traffic-generator", "control_enabled": bool(control_token)})

    def metrics(request: Request) -> Response:
        return PlainTextResponse(eng.registry.render(), media_type=CONTENT_TYPE)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        eng.start()
        if initial != "stopped":
            eng.set_scenario(initial)
        if not control_token:
            logger.warning("CONTROL_TOKEN is not set: the control API is disabled")
        try:
            yield
        finally:
            eng.stop()

    return Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Route("/control/status", status, methods=["GET"]),
        Route("/control/scenario", scenario, methods=["POST"]),
        Route("/control/users", users, methods=["POST"]),
    ], lifespan=lifespan)


def main() -> None:
    import uvicorn

    from common.logging_utils import configure_logging

    configure_logging(get_str("LOG_LEVEL", "INFO"), get_str("LOG_FORMAT", "text"))
    uvicorn.run(create_app(), host=get_str("GENERATOR_HOST", "0.0.0.0"),  # noqa: S104 - container
                port=get_int(("GENERATOR_PORT", "PORT"), 8089), log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
