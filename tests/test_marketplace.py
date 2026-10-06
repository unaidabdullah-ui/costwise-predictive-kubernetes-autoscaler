"""COSTWISE MARKETPLACE served by a real uvicorn server on loopback."""

import requests

from common.promtext import parse_samples
from marketplace import catalog
from marketplace.app import Settings, create_app, encode_cart, parse_cart, route_template
from tests.conftest import serve


def settings(**kw):
    s = Settings()
    s.work_scale = 0.0  # no artificial CPU work in unit tests
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_catalogue_is_original_and_complete():
    items = catalog.products()
    assert len(items) == 48 and len({p.id for p in items}) == 48
    assert {p.category for p in items} == {c["id"] for c in catalog.CATEGORIES}
    assert all(p.price > 0 and 3.0 <= p.rating <= 5.0 and p.mrp >= p.price for p in items)
    assert catalog.search("wireless earbuds")[0].name == "Aurora Wireless Earbuds"


def test_route_templates_are_bounded():
    assert route_template("/api/products/1001") == "/api/products/{id}"
    assert route_template("/products/abc") == "/api/products/{id}"
    assert route_template("/search") == "/api/search"
    assert route_template("/static/store.js") == "/static"
    assert route_template("/wp-admin") == "other"


def test_cart_cookie_roundtrip_ignores_garbage():
    cart = parse_cart("1001-2.9999-1.abc.1002-99")
    assert cart == {1001: 2, 1002: 10}
    assert parse_cart(encode_cart(cart)) == cart


def test_shopping_flow_and_metrics():
    with serve(create_app(settings())) as url:
        s = requests.Session()
        assert s.get(f"{url}/health").json()["status"] == "ok"
        assert len(s.get(f"{url}/api/products?limit=5").json()["items"]) == 5
        assert s.get(f"{url}/products?category=books").json()["total"] == 6  # alias without /api
        assert s.get(f"{url}/api/products/1001").json()["product"]["name"] == "Aurora Wireless Earbuds"
        assert s.get(f"{url}/api/products/9").status_code == 404
        assert s.get(f"{url}/api/search", params={"q": "tea"}).json()["total"] >= 1
        assert len(s.get(f"{url}/api/categories").json()["items"]) == 8
        assert all(p["discount_pct"] >= 20 for p in s.get(f"{url}/api/deals").json()["items"])
        assert s.post(f"{url}/api/checkout", json={}).status_code == 400  # empty cart
        r = s.post(f"{url}/api/cart", json={"product_id": 1001, "quantity": 2})
        assert r.json()["count"] == 2 and r.headers["x-served-by"]
        assert s.post(f"{url}/api/cart", json={"product_id": 1, "quantity": 1}).status_code == 404
        order = s.post(f"{url}/api/checkout", json={"name": "Tester"}).json()
        assert order["status"] == "confirmed" and order["total"] == 2 * 2499
        assert s.get(f"{url}/api/cart").json()["count"] == 0
        assert s.get(f"{url}/api/orders").json()["items"][0]["order_id"] == order["order_id"]
        assert s.get(f"{url}/").status_code == 200 and "COSTWISE" in s.get(f"{url}/").text.upper()
        m = parse_samples(s.get(f"{url}/metrics").text)
        assert m['marketplace_http_requests_total{route="/api/checkout",method="POST",status="201"}'] == 1
        assert m["marketplace_orders_total"] == 1


def test_overload_sheds_with_503():
    with serve(create_app(settings(max_inflight=1, work_scale=200.0))) as url:
        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(8) as pool:
            codes = list(pool.map(lambda _: requests.get(f"{url}/api/search?q=tea", timeout=10).status_code,
                                  range(8)))
        assert 503 in codes and 200 in codes
        m = parse_samples(requests.get(f"{url}/metrics").text)
        assert m["marketplace_rejected_requests_total"] >= 1
