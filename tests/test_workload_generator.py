import random
import time

import pytest
import requests

from marketplace.app import Settings
from marketplace.app import create_app as create_store
from tests.conftest import serve
from workload_generator.app import create_app
from workload_generator.engine import ACTION_MIX, EngineSettings, TrafficEngine, poisson
from workload_generator.plans import FLASH_PEAK_SHOPPERS, NORMAL_SHOPPERS, Plan


def test_action_mix_sums_to_one():
    assert sum(w for _, w in ACTION_MIX) == pytest.approx(1.0)


def test_plans_have_the_documented_shape():
    assert Plan("normal", 0).shoppers(60) == pytest.approx(NORMAL_SHOPPERS, rel=0.12)
    flash = Plan("flash_sale", NORMAL_SHOPPERS)
    assert flash.shoppers(10) < 300 and flash.shoppers(100) > 0.9 * FLASH_PEAK_SHOPPERS
    assert flash.shoppers(300) < 500
    assert Plan("stopped", 800).shoppers(10) == 0
    assert Plan("users", 100, target=400).shoppers(20) == pytest.approx(400)
    with pytest.raises(ValueError):
        Plan("nope")


def test_poisson_mean():
    rng = random.Random(1)
    for lam in (0.5, 5.0, 50.0):
        mean = sum(poisson(rng, lam) for _ in range(4000)) / 4000
        assert mean == pytest.approx(lam, rel=0.08)


def test_requests_for_every_action_are_valid_shop_calls():
    eng = TrafficEngine(EngineSettings(target_url="http://127.0.0.1:9"))
    rng = random.Random(0)
    for action, _ in ACTION_MIX:
        method, path, body, headers = eng.request_for(action, rng)
        assert path.startswith("/api/") and method in ("GET", "POST")
        if action in ("add_to_cart", "checkout", "view_cart"):
            assert headers["Cookie"].startswith("cw_cart=")


def test_control_api_requires_the_token_and_sends_real_traffic():
    store_settings = Settings()
    store_settings.work_scale = 0.0
    with serve(create_store(store_settings)) as store:
        eng = TrafficEngine(EngineSettings(target_url=store, workers=8))
        with serve(create_app(eng, token="t0ken", initial_scenario="stopped")) as gen:
            assert requests.post(f"{gen}/control/scenario", json={"scenario": "busy"}).status_code == 403
            assert requests.post(f"{gen}/control/scenario", json={"scenario": "busy"},
                                 headers={"X-Control-Token": "wrong"}).status_code == 403
            r = requests.post(f"{gen}/control/users", json={"add": 3000}, headers={"X-Control-Token": "t0ken"})
            assert r.status_code == 200 and r.json()["scenario"] == "users"
            assert requests.post(f"{gen}/control/scenario", json={"scenario": "x"},
                                 headers={"X-Control-Token": "t0ken"}).status_code == 400
            time.sleep(4)
            st = requests.get(f"{gen}/control/status").json()
            assert st["totals"]["sent"] > 20 and st["totals"]["errors"] == 0
            assert "t0ken" not in requests.get(f"{gen}/control/status").text
            assert "generator_active_shoppers" in requests.get(f"{gen}/metrics").text


def test_control_disabled_without_token():
    eng = TrafficEngine(EngineSettings(target_url="http://127.0.0.1:9", workers=1))
    with serve(create_app(eng, token="")) as gen:
        r = requests.post(f"{gen}/control/scenario", json={"scenario": "busy"}, headers={"X-Control-Token": ""})
        assert r.status_code == 503
