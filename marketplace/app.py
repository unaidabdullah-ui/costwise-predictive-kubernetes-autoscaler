"""COSTWISE MARKETPLACE - the demo online store that CostWise autoscales.

A small Starlette application (one process per pod) that serves:

* the storefront UI at ``/`` (static HTML/JS/CSS, original design, no external assets);
* a JSON shop API: ``/api/products``, ``/api/products/{id}``, ``/api/search``,
  ``/api/categories``, ``/api/deals``, ``/api/cart`` (GET/POST/DELETE),
  ``/api/checkout`` (POST) and ``/api/orders`` - each also reachable without the
  ``/api`` prefix (``/products``, ``/search`` ...);
* ``/health`` (liveness/readiness) and ``/metrics`` (Prometheus text format).

Load is *real*: every shop request performs a fixed amount of CPU work
(``ROUTE_COST_MS``, scaled by ``MARKETPLACE_WORK_SCALE``) measured with the
thread's CPU clock, so a pod limited to 250m CPU really saturates at roughly
20 requests/second and latency really rises when it is overloaded. When more
than ``MARKETPLACE_MAX_INFLIGHT`` requests are in progress the pod sheds load
with HTTP 503, which Prometheus records as errors.

Cart: kept in the ``cw_cart`` cookie (``id:qty`` pairs), so any pod can serve any
request - prices are always recomputed from the catalogue on the server, so
editing the cookie cannot change a price. Orders: kept in memory in the pod that
served the checkout (this is a demo; a real store would use a database).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.env import get_float, get_int, get_str
from common.promtext import CONTENT_TYPE, Registry
from marketplace import catalog

VERSION = "2.1.0"
STATIC_DIR = Path(__file__).resolve().parent / "static"
POD_NAME = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or socket.gethostname()
logger = logging.getLogger("marketplace")

# CPU milliseconds of work per request type (before MARKETPLACE_WORK_SCALE).
ROUTE_COST_MS: dict[str, float] = {
    "/api/products": 8.0,
    "/api/products/{id}": 6.0,
    "/api/search": 14.0,
    "/api/deals": 8.0,
    "/api/categories": 4.0,
    "/api/cart:POST": 6.0,
    "/api/cart:GET": 4.0,
    "/api/cart:DELETE": 3.0,
    "/api/checkout": 20.0,
    "/api/orders": 5.0,
}
API_ROUTES = ("/api/products", "/api/products/{id}", "/api/search", "/api/deals", "/api/categories",
              "/api/cart", "/api/checkout", "/api/orders")
MAX_CART_LINES = 20
MAX_QTY = 10
MAX_ORDERS_IN_MEMORY = 2000


class Settings:
    def __init__(self) -> None:
        self.work_scale = max(0.0, get_float("MARKETPLACE_WORK_SCALE", 1.0))
        self.max_inflight = max(1, get_int("MARKETPLACE_MAX_INFLIGHT", 24))
        self.max_work_seconds = max(0.05, get_float("MARKETPLACE_MAX_WORK_SECONDS", 2.0))
        self.log_level = get_str("LOG_LEVEL", "INFO")


# --------------------------------------------------------------------------- metrics
registry = Registry(process_metrics=True)
REQUESTS = registry.counter("marketplace_http_requests", "HTTP requests handled by the marketplace.",
                            ("route", "method", "status"))
DURATION = registry.histogram("marketplace_http_request_duration_seconds", "Request latency in seconds.",
                              ("route",), buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0))
INFLIGHT = registry.gauge("marketplace_inflight_requests", "Requests currently being processed.")
REJECTED = registry.counter("marketplace_rejected_requests", "Requests rejected with 503 (pod overloaded).")
ORDERS = registry.counter("marketplace_orders", "Orders placed (checkout simulation).")
CART_ADDS = registry.counter("marketplace_cart_additions", "Items added to carts.")
REVENUE = registry.counter("marketplace_order_value_rupees", "Sum of simulated order values in rupees.")
INFO = registry.gauge("marketplace_info", "Static information about this marketplace pod.", ("version", "pod"))
INFO.labels(version=VERSION, pod=POD_NAME).set(1)
for _route in API_ROUTES:  # pre-register so rate() works from the first scrape
    for _method in (("GET", "POST", "DELETE") if _route == "/api/cart" else
                    ("POST",) if _route == "/api/checkout" else ("GET",)):
        REQUESTS.labels(route=_route, method=_method, status="200")
    DURATION.labels(route=_route)

_ID_RE = re.compile(r"^/(?:api/)?products/[^/]+$")


def route_template(path: str) -> str:
    """Map a request path to a bounded route label (aliases map to their /api route)."""
    if path in ("/health", "/healthz", "/ready", "/metrics"):
        return path
    if _ID_RE.match(path):
        return "/api/products/{id}"
    bare = path[4:] if path.startswith("/api/") else path
    candidate = "/api" + bare.rstrip("/")
    if candidate in API_ROUTES:
        return candidate
    if path == "/" or path.startswith("/static/") or path == "/favicon.svg":
        return "/static"
    return "other"


class MetricsMiddleware:
    """Pure ASGI middleware: in-flight limit, latency histogram and request counter."""

    def __init__(self, app: ASGIApp, max_inflight: int) -> None:
        self.app = app
        self.max_inflight = max_inflight
        self._inflight = 0
        self._lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        route = route_template(scope.get("path", ""))
        method = scope.get("method", "GET")
        shop = route.startswith("/api/")
        if shop:
            with self._lock:
                overloaded = self._inflight >= self.max_inflight
                if not overloaded:
                    self._inflight += 1
            if overloaded:
                REJECTED.inc()
                REQUESTS.labels(route=route, method=method, status="503").inc()
                response = JSONResponse({"error": "busy", "detail": "This pod is overloaded; please retry."},
                                        status_code=503, headers={"Retry-After": "1", "X-Served-By": POD_NAME})
                await response(scope, receive, send)
                return
            INFLIGHT.inc()
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-served-by", POD_NAME.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if shop:
                with self._lock:
                    self._inflight -= 1
                INFLIGHT.dec()
                DURATION.labels(route=route).observe(time.perf_counter() - started)
            if route != "/metrics":
                REQUESTS.labels(route=route, method=method, status=str(status["code"])).inc()


def burn_cpu(milliseconds: float, max_seconds: float) -> None:
    """Consume ``milliseconds`` of *CPU time* on this thread (wall time grows when throttled)."""
    if milliseconds <= 0:
        return
    target = time.thread_time() + milliseconds / 1000.0
    deadline = time.perf_counter() + max_seconds
    x = 0x9E3779B9
    while time.thread_time() < target and time.perf_counter() < deadline:
        for _ in range(200):
            x = (x * 1103515245 + 12345) & 0xFFFFFFFF


# --------------------------------------------------------------------------- cart helpers
def parse_cart(raw: str | None) -> dict[int, int]:
    cart: dict[int, int] = {}
    if not raw:
        return cart
    for part in raw.split(".")[:MAX_CART_LINES]:
        pid, _, qty = part.partition("-")
        try:
            pid_i, qty_i = int(pid), int(qty)
        except ValueError:
            continue
        if pid_i in catalog.by_id() and qty_i > 0:
            cart[pid_i] = min(qty_i, MAX_QTY)
    return cart


def encode_cart(cart: dict[int, int]) -> str:
    return ".".join(f"{pid}-{qty}" for pid, qty in list(cart.items())[:MAX_CART_LINES] if qty > 0)


def cart_view(cart: dict[int, int]) -> dict[str, Any]:
    lines = []
    subtotal = 0
    for pid, qty in cart.items():
        p = catalog.by_id()[pid]
        lines.append({"product": p.card(), "quantity": qty, "line_total": p.price * qty})
        subtotal += p.price * qty
    delivery = 0 if subtotal == 0 or subtotal >= 499 else 40
    return {"items": lines, "count": sum(cart.values()), "subtotal": subtotal, "delivery": delivery,
            "total": subtotal + delivery, "currency": "INR"}


async def json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    if len(raw) > 16_384:
        raise ValueError("request body too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON object expected")
    return data


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- application
def create_app(settings: Settings | None = None) -> Starlette:
    s = settings or Settings()
    orders: OrderedDict[str, dict[str, Any]] = OrderedDict()
    orders_lock = threading.Lock()
    rng = random.Random()

    def work(key: str) -> None:
        burn_cpu(ROUTE_COST_MS.get(key, 4.0) * s.work_scale, s.max_work_seconds)

    def session_of(request: Request) -> tuple[str, bool]:
        sid = request.cookies.get("cw_session", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{8,40}", sid):
            return sid, False
        return secrets.token_urlsafe(12), True

    def respond(request: Request, data: Any, status: int = 200, cart: dict[int, int] | None = None) -> JSONResponse:
        resp = JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})
        sid, new = session_of(request)
        if new:
            resp.set_cookie("cw_session", sid, max_age=7 * 86400, httponly=True, samesite="lax")
        if cart is not None:
            resp.set_cookie("cw_cart", encode_cart(cart), max_age=7 * 86400, httponly=False, samesite="lax")
        return resp

    def list_products(request: Request) -> JSONResponse:
        work("/api/products")
        q = request.query_params
        items = list(catalog.products())
        category = q.get("category")
        if category:
            items = [p for p in items if p.category == category]
        sort = q.get("sort", "featured")
        if sort == "price_asc":
            items.sort(key=lambda p: p.price)
        elif sort == "price_desc":
            items.sort(key=lambda p: -p.price)
        elif sort == "rating":
            items.sort(key=lambda p: (-p.rating, -p.reviews))
        elif sort == "discount":
            items.sort(key=lambda p: -p.discount_pct)
        limit = _int(q.get("limit"), 24, 1, 48)
        page = _int(q.get("page"), 1, 1, 100)
        start = (page - 1) * limit
        return respond(request, {"items": [p.card() for p in items[start:start + limit]], "total": len(items),
                                 "page": page, "limit": limit, "served_by": POD_NAME})

    def product_detail(request: Request) -> JSONResponse:
        work("/api/products/{id}")
        try:
            product = catalog.by_id()[int(request.path_params["pid"])]
        except (KeyError, ValueError):
            return respond(request, {"error": "not_found", "detail": "No such product."}, 404)
        related = [p.card() for p in catalog.products() if p.category == product.category and p.id != product.id][:4]
        return respond(request, {"product": product.detail(), "related": related, "served_by": POD_NAME})

    def search(request: Request) -> JSONResponse:
        work("/api/search")
        query = request.query_params.get("q", "")[:80]
        results = catalog.search(query)
        return respond(request, {"query": query, "items": [p.card() for p in results], "total": len(results),
                                 "served_by": POD_NAME})

    def categories(request: Request) -> JSONResponse:
        work("/api/categories")
        counts: dict[str, int] = {}
        for p in catalog.products():
            counts[p.category] = counts.get(p.category, 0) + 1
        return respond(request, {"items": [{**c, "products": counts.get(c["id"], 0)} for c in catalog.CATEGORIES]})

    def deals(request: Request) -> JSONResponse:
        work("/api/deals")
        items = sorted((p for p in catalog.products() if p.discount_pct >= 20), key=lambda p: -p.discount_pct)
        ends = int(time.time() // 3600 + 1) * 3600  # deals "end" at the top of the next hour
        return respond(request, {"items": [p.card() for p in items], "ends_at": ends, "served_by": POD_NAME})

    async def cart(request: Request) -> JSONResponse:
        current = parse_cart(request.cookies.get("cw_cart"))
        if request.method == "GET":
            work("/api/cart:GET")
            return respond(request, cart_view(current))
        if request.method == "DELETE":
            work("/api/cart:DELETE")
            return respond(request, cart_view({}), cart={})
        try:
            body = await json_body(request)
            pid = int(body.get("product_id", 0))
        except (ValueError, TypeError):
            return respond(request, {"error": "bad_request", "detail": "Send {\"product_id\": <id>, "
                                                                        "\"quantity\": <n>}."}, 400)
        if pid not in catalog.by_id():
            return respond(request, {"error": "not_found", "detail": "No such product."}, 404)
        work("/api/cart:POST")
        mode = body.get("mode", "add")
        qty = _int(body.get("quantity"), 1, 0, MAX_QTY)
        if mode == "set":
            new_qty = qty
        else:
            new_qty = min(MAX_QTY, current.get(pid, 0) + max(1, qty))
            CART_ADDS.inc(max(1, qty))
        if new_qty <= 0:
            current.pop(pid, None)
        elif pid in current or len(current) < MAX_CART_LINES:
            current[pid] = new_qty
        return respond(request, cart_view(current), cart=current)

    async def checkout(request: Request) -> JSONResponse:
        try:
            body = await json_body(request)
        except (ValueError, TypeError):
            return respond(request, {"error": "bad_request", "detail": "Invalid JSON."}, 400)
        current = parse_cart(request.cookies.get("cw_cart"))
        if not current:
            return respond(request, {"error": "empty_cart", "detail": "Your cart is empty."}, 400)
        work("/api/checkout")
        view = cart_view(current)
        sid, _ = session_of(request)
        order_id = f"CW-{time.strftime('%y%m%d')}-{rng.randint(100000, 999999)}"
        order = {
            "order_id": order_id, "placed_at": time.time(), "status": "confirmed",
            "name": str(body.get("name", "Guest shopper"))[:60] or "Guest shopper",
            "payment": str(body.get("payment", "upi"))[:20], "items": view["items"], "count": view["count"],
            "subtotal": view["subtotal"], "delivery": view["delivery"], "total": view["total"], "currency": "INR",
            "estimated_delivery_days": rng.randint(2, 5), "served_by": POD_NAME,
            "note": "Simulated order - no payment is taken and nothing is shipped.",
        }
        with orders_lock:
            orders[order_id] = {**order, "session": sid}
            while len(orders) > MAX_ORDERS_IN_MEMORY:
                orders.popitem(last=False)
        ORDERS.inc()
        REVENUE.inc(view["total"])
        return respond(request, order, 201, cart={})

    def list_orders(request: Request) -> JSONResponse:
        work("/api/orders")
        sid, _ = session_of(request)
        with orders_lock:
            mine = [{k: v for k, v in o.items() if k != "session"} for o in orders.values() if o["session"] == sid]
        return respond(request, {"items": list(reversed(mine))[:20], "served_by": POD_NAME,
                                 "note": "Orders are kept in memory by the pod that took them (demo store)."})

    def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "marketplace", "version": VERSION, "pod": POD_NAME},
                            headers={"Cache-Control": "no-store"})

    def metrics(request: Request) -> Response:
        return PlainTextResponse(registry.render(), media_type=CONTENT_TYPE)

    def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    def api_index(request: Request) -> JSONResponse:
        return JSONResponse({"service": "COSTWISE MARKETPLACE API", "version": VERSION, "routes": list(API_ROUTES)})

    shop_routes: list[Route] = []
    for prefix in ("/api", ""):
        shop_routes += [
            Route(f"{prefix}/products", list_products, methods=["GET"]),
            Route(f"{prefix}/products/{{pid}}", product_detail, methods=["GET"]),
            Route(f"{prefix}/search", search, methods=["GET"]),
            Route(f"{prefix}/categories", categories, methods=["GET"]),
            Route(f"{prefix}/deals", deals, methods=["GET"]),
            Route(f"{prefix}/cart", cart, methods=["GET", "POST", "DELETE"]),
            Route(f"{prefix}/checkout", checkout, methods=["POST"]),
            Route(f"{prefix}/orders", list_orders, methods=["GET"]),
        ]
    routes = [
        Route("/", index, methods=["GET"]),
        Route("/api", api_index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/ready", health, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        *shop_routes,
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        logger.info("COSTWISE MARKETPLACE %s ready on pod %s (work scale %.2f, max in-flight %d)",
                    VERSION, POD_NAME, s.work_scale, s.max_inflight)
        yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(MetricsMiddleware, max_inflight=s.max_inflight)
    return app


def main() -> None:
    import uvicorn

    from common.logging_utils import configure_logging

    settings = Settings()
    configure_logging(settings.log_level, get_str("LOG_FORMAT", "text"))
    port = get_int(("MARKETPLACE_PORT", "PORT"), 8080)
    host = get_str("MARKETPLACE_HOST", "0.0.0.0")  # noqa: S104 - container listens on all interfaces
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning", access_log=False,
                timeout_keep_alive=5)


if __name__ == "__main__":
    main()
