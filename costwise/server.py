"""CostWise control-room web server (Starlette), bound to 127.0.0.1:8000 by default.

Read endpoints (JSON): ``/health``, ``/api/dashboard``, ``/forecast``,
``/scaling-decision``, ``/cost``, ``/pods``, ``/events``, ``/api/technical``,
``/api/model``; Prometheus text: ``/metrics``.

Action endpoints (POST): ``/demo/start``, ``/demo/traffic``, ``/demo/users``,
``/demo/flash-sale``, ``/demo/busy``, ``/demo/stop``, ``/demo/reset``,
``/demo/settings``, ``/model/retrain``.

Security (the browser never gets Kubernetes access):

* only a fixed set of demo actions exists - there is no generic proxy;
* every POST must carry the header ``X-CostWise: 1`` (a custom header cannot be
  sent cross-site without a CORS pre-flight, which this server never allows)
  and, if the browser sends an ``Origin``, it must be this dashboard;
* the ``Host`` header must be a loopback name (DNS-rebinding protection);
* strict security headers (CSP ``default-src 'self'``, no framing, no sniffing);
* secrets (control token, service-account token) are never returned or logged.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from autoscaler.config import ConfigError
from common.jsonutil import to_jsonable
from common.promtext import CONTENT_TYPE
from costwise.loadgen import GeneratorError
from costwise.runtime import Runtime

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class SecurityMiddleware:
    """Host allow-list, CSRF guard for POST, security headers."""

    def __init__(self, app: ASGIApp, allowed_hosts: tuple[str, ...], port: int) -> None:
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.port = port

    def _host_ok(self, host: str) -> bool:
        host = host.lower().strip().rstrip("/")
        if host.startswith("["):  # [::1]:8000
            name = host.split("]", 1)[0] + "]"
        elif host.count(":") == 1:  # 127.0.0.1:8000
            name = host.split(":", 1)[0]
        else:
            name = host
        return bool(name) and (name in self.allowed_hosts or host in self.allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not self._host_ok(headers.get("host", "")):
            await JSONResponse({"error": "forbidden_host"}, 403)(scope, receive, send)
            return
        if scope.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
            origin = headers.get("origin")
            if headers.get("x-costwise") != "1":
                await JSONResponse({"error": "forbidden", "detail": "missing X-CostWise header"}, 403)(
                    scope, receive, send)
                return
            if origin and not self._host_ok(origin.split("://", 1)[-1]):
                await JSONResponse({"error": "forbidden", "detail": "cross-origin request"}, 403)(scope, receive, send)
                return

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                present = {k.lower() for k, _ in hdrs}
                for k, v in SECURITY_HEADERS.items():
                    if k.lower().encode() not in present:
                        hdrs.append((k.lower().encode(), v.encode()))
                message["headers"] = hdrs
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(to_jsonable(data), status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 8192:
        raise ValueError("request body too large")
    data = json.loads(raw or b"{}")
    if not isinstance(data, dict):
        raise ValueError("a JSON object is expected")
    return data


def create_app(runtime: Runtime, start_runtime: bool = True) -> Starlette:
    rt = runtime

    async def action(fn: Callable[[], Any]) -> JSONResponse:
        try:
            result = await run_in_threadpool(fn)
        except GeneratorError as exc:
            return _json({"ok": False, "error": str(exc), "hint": exc.hint}, 503)
        except (ValueError, TypeError, ConfigError) as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        return _json(result if isinstance(result, dict) and "ok" in result else {"ok": True, "result": result})

    def handler(fn: Callable[[Request], Awaitable[JSONResponse]]) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def wrapped(request: Request) -> JSONResponse:
            try:
                return await fn(request)
            except (ValueError, json.JSONDecodeError) as exc:
                return _json({"ok": False, "error": str(exc)}, 400)
        return wrapped

    # --------------------------------------------------------------- pages
    def page(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    # --------------------------------------------------------------- read API
    async def health(request: Request) -> JSONResponse:
        data = await run_in_threadpool(rt.health)
        return _json(data, 200)

    async def dashboard(request: Request) -> JSONResponse:
        return _json(await run_in_threadpool(rt.dashboard))

    async def forecast(request: Request) -> JSONResponse:
        d = await run_in_threadpool(rt.dashboard)
        return _json({**d["forecast"], "series": d["series"]["future"]})

    async def decision(request: Request) -> JSONResponse:
        d = await run_in_threadpool(rt.dashboard)
        return _json({"decision": d["decision"], "why": d["why"]})

    async def cost(request: Request) -> JSONResponse:
        return _json((await run_in_threadpool(rt.dashboard))["cost"])

    async def pods(request: Request) -> JSONResponse:
        return _json((await run_in_threadpool(rt.dashboard))["kubernetes"])

    async def events(request: Request) -> JSONResponse:
        try:
            after = int(request.query_params.get("after", "0"))
        except ValueError:
            after = 0
        return _json({"events": [e.to_dict() for e in rt.events.since(after, 200)]})

    async def technical(request: Request) -> JSONResponse:
        return _json(await run_in_threadpool(rt.technical))

    async def model(request: Request) -> JSONResponse:
        return _json(await run_in_threadpool(rt.models.info))

    def metrics(request: Request) -> Response:
        return PlainTextResponse(rt.publisher.exposition(), media_type=CONTENT_TYPE)

    # --------------------------------------------------------------- actions
    @handler
    async def demo_start(request: Request) -> JSONResponse:
        return await action(rt.start_demo)

    @handler
    async def demo_traffic(request: Request) -> JSONResponse:
        scenario = str((await _body(request)).get("scenario", ""))
        return await action(lambda: {"ok": True, "traffic": rt.set_traffic(scenario)})

    @handler
    async def demo_users(request: Request) -> JSONResponse:
        return await action(lambda: {"ok": True, "traffic": rt.set_traffic("users")})

    @handler
    async def demo_flash(request: Request) -> JSONResponse:
        return await action(lambda: {"ok": True, "traffic": rt.set_traffic("flash_sale")})

    @handler
    async def demo_busy(request: Request) -> JSONResponse:
        return await action(lambda: {"ok": True, "traffic": rt.set_traffic("busy")})

    @handler
    async def demo_stop(request: Request) -> JSONResponse:
        return await action(rt.stop_traffic)

    @handler
    async def demo_reset(request: Request) -> JSONResponse:
        return await action(rt.reset)

    @handler
    async def demo_settings(request: Request) -> JSONResponse:
        changes = await _body(request)
        return await action(lambda: rt.apply_settings(changes))

    @handler
    async def model_retrain(request: Request) -> JSONResponse:
        return await action(lambda: {"ok": True, "retrain": rt.retrain()})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if start_runtime:
            rt.start()
        try:
            yield
        finally:
            if start_runtime:
                rt.stop()

    routes = [
        Route("/", page, methods=["GET"]),
        Route("/technical", page, methods=["GET"]),
        Route("/architecture", page, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Route("/api/dashboard", dashboard, methods=["GET"]),
        Route("/api/technical", technical, methods=["GET"]),
        Route("/api/model", model, methods=["GET"]),
        Route("/forecast", forecast, methods=["GET"]),
        Route("/scaling-decision", decision, methods=["GET"]),
        Route("/cost", cost, methods=["GET"]),
        Route("/pods", pods, methods=["GET"]),
        Route("/events", events, methods=["GET"]),
        Route("/demo/start", demo_start, methods=["POST"]),
        Route("/demo/traffic", demo_traffic, methods=["POST"]),
        Route("/demo/users", demo_users, methods=["POST"]),
        Route("/demo/flash-sale", demo_flash, methods=["POST"]),
        Route("/demo/busy", demo_busy, methods=["POST"]),
        Route("/demo/stop", demo_stop, methods=["POST"]),
        Route("/demo/reset", demo_reset, methods=["POST"]),
        Route("/demo/settings", demo_settings, methods=["POST"]),
        Route("/model/retrain", model_retrain, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(SecurityMiddleware, allowed_hosts=rt.settings.allowed_hosts, port=rt.settings.port)
    return app
