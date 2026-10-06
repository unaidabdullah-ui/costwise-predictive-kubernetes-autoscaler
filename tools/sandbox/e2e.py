"""End-to-end faculty-demo validation in the sandbox (no Docker/Minikube available here).

Real components: marketplace processes (250m CPU each via CFS quota), traffic generator
service, Prometheus 2.53.2, the CostWise control plane (controller + dashboard) and a
Chromium browser driven by Playwright that clicks the dashboard buttons.
Emulated: Kubernetes (ProcessScaler/ProcessClusterView) and the Service (TCP proxy).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(HERE))
os.chdir(PROJECT)

from podsim import ProcessCluster, ProcessClusterView, ProcessScaler, RoundRobinProxy  # noqa: E402

OUT = Path(os.environ.get("SANDBOX_OUT", str(HERE / "out")))
SHOTS = OUT / "screenshots"
PROM_DIR = Path(os.environ.get("PROM_DIR", "/opt/prometheus"))  # folder with the prometheus binary
DASH = "http://127.0.0.1:18000"
LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.append(line)


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - fixed loopback URLs
        return json.loads(r.read())


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    SHOTS.mkdir(parents=True)
    work = OUT / "cluster"
    procs: list[subprocess.Popen] = []
    cluster = ProcessCluster(work, base_port=18101, initial=1, readiness_delay=5.0)
    RoundRobinProxy(cluster, 18080)

    prom_cfg = OUT / "prometheus.yml"
    prom_cfg.write_text(f"""
global: {{scrape_interval: 5s, evaluation_interval: 5s}}
rule_files: ["{PROJECT}/prometheus/rules/costwise-alerts.yml"]
scrape_configs:
  - job_name: marketplace
    file_sd_configs: [{{files: ["{cluster.sd_file}"], refresh_interval: 5s}}]
  - job_name: traffic-generator
    static_configs: [{{targets: ["127.0.0.1:18089"]}}]
""")
    procs.append(subprocess.Popen([str(PROM_DIR / "prometheus"), f"--config.file={prom_cfg}",
                                   f"--storage.tsdb.path={OUT / 'tsdb'}", "--web.listen-address=127.0.0.1:19090",
                                   "--storage.tsdb.retention.time=2h"],
                                  stdout=(OUT / "prometheus.log").open("w"), stderr=subprocess.STDOUT))
    token = secrets.token_urlsafe(24)
    genv = {**os.environ, "TARGET_URL": "http://127.0.0.1:18080", "CONTROL_TOKEN": token, "GENERATOR_PORT": "18089",
            "GENERATOR_HOST": "127.0.0.1", "PYTHONPATH": str(PROJECT)}
    procs.append(subprocess.Popen([sys.executable, "-m", "workload_generator"], cwd=PROJECT, env=genv,
                                  stdout=(OUT / "generator.log").open("w"), stderr=subprocess.STDOUT))

    model_dir = OUT / "model"
    model_dir.mkdir()
    for ext in (".npz", ".json"):
        shutil.copy(PROJECT / "ml" / "models" / f"lstm_lite{ext}", model_dir / f"lstm_lite{ext}")
    os.environ["MODEL_PATH"] = str(model_dir / "lstm_lite.npz")
    os.environ.update({"PROMETHEUS_URL": "http://127.0.0.1:19090", "CONTROL_TOKEN": token,
                       "GENERATOR_URL": "http://127.0.0.1:18089", "MARKETPLACE_URL": "http://127.0.0.1:18080",
                       "PROMETHEUS_PUBLIC_URL": "http://127.0.0.1:19090", "COSTWISE_STATE_DIR": str(OUT / "state"),
                       "DASHBOARD_PORT": "18000", "LOG_FORMAT": "json"})
    from autoscaler.config import AutoscalerConfig
    from common.logging_utils import configure_logging
    from costwise.runtime import build_runtime
    from costwise.server import create_app
    from costwise.settings import DashboardSettings

    configure_logging("INFO", "json", str(OUT / "control-plane.log"))
    settings = DashboardSettings.from_env()
    runtime = build_runtime(settings, AutoscalerConfig.from_env(), scaler=ProcessScaler(cluster),
                            cluster=ProcessClusterView(cluster))
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=18000, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(60):
        try:
            get(f"{DASH}/health")
            break
        except OSError:
            time.sleep(0.5)
    log("control plane up")

    samples: list[dict] = []
    stop_sampling = threading.Event()

    def sampler() -> None:
        while not stop_sampling.is_set():
            try:
                d = get(f"{DASH}/api/dashboard")
                samples.append({"t": d["time"], "shoppers": d["traffic"]["shoppers"],
                                "scenario": d["traffic"]["scenario"], "rps": d["metrics"]["request_rate"],
                                "p95": d["metrics"]["latency_p95_ms"], "err": d["metrics"]["error_ratio"],
                                "pods": d["kubernetes"]["desired_replicas"], "ready": d["kubernetes"]["ready_replicas"],
                                "peak": d["forecast"]["peak"], "safe": d["forecast"]["safe_mode"],
                                "required": (d["decision"] or {}).get("required_replicas"),
                                "action": (d["decision"] or {}).get("action"),
                                "cost": d["cost"]["current_hourly"], "health": d["health"]["status"]})
            except Exception as exc:  # noqa: BLE001
                samples.append({"t": time.time(), "error": str(exc)})
            stop_sampling.wait(2)

    threading.Thread(target=sampler, daemon=True).start()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(DASH + "/")
        page.wait_for_selector("#m-status-v")
        time.sleep(4)
        page.screenshot(path=str(SHOTS / "01-idle.png"), full_page=True)

        def click(label: str) -> None:
            page.get_by_role("button", name=label).click()
            log(f"clicked {label}")

        click("START DEMO")
        time.sleep(100)
        page.screenshot(path=str(SHOTS / "02-normal-traffic.png"), full_page=True)
        click("FLASH SALE")
        t_flash = time.time()
        for secs, name in ((35, "03-flash-forecast-rising"), (70, "04-flash-scaling"), (130, "05-flash-peak")):
            time.sleep(max(0, t_flash + secs - time.time()))
            page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
        # presentation mode at peak
        page.keyboard.press("p")
        time.sleep(3)
        page.screenshot(path=str(SHOTS / "06-presentation-mode.png"), full_page=False)
        page.keyboard.press("p")
        time.sleep(max(0, t_flash + 200 - time.time()))
        click("STOP TRAFFIC")
        time.sleep(110)
        page.screenshot(path=str(SHOTS / "07-after-stop-scaled-down.png"), full_page=True)
        page.goto(DASH + "/#/technical")
        time.sleep(4)
        page.screenshot(path=str(SHOTS / "08-technical.png"), full_page=True)
        page.goto(DASH + "/#/architecture")
        time.sleep(2)
        page.screenshot(path=str(SHOTS / "09-architecture.png"), full_page=True)
        page.goto(DASH + "/")
        time.sleep(3)
        click("RETRAIN MODEL")
        for _ in range(120):
            st = get(f"{DASH}/api/model")["retrain"]
            if st.get("status") in ("succeeded", "failed"):
                break
            time.sleep(2)
        log(f"retrain: {st}")
        click("RESET DEMO")
        time.sleep(12)
        page.screenshot(path=str(SHOTS / "10-after-reset.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 800})
        page.screenshot(path=str(SHOTS / "11-dashboard-1280.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        time.sleep(2)
        page.screenshot(path=str(SHOTS / "12-dashboard-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1400, "height": 900})
        # storefront through the Service proxy
        page.goto("http://127.0.0.1:18080/")
        time.sleep(2)
        page.screenshot(path=str(SHOTS / "13-store-home.png"), full_page=True)
        page.goto("http://127.0.0.1:18080/#/p/1007")
        time.sleep(1.5)
        page.get_by_role("button", name="Add to cart").click()
        time.sleep(1)
        page.screenshot(path=str(SHOTS / "14-store-product.png"))
        page.goto("http://127.0.0.1:18080/#/checkout")
        time.sleep(1.5)
        page.get_by_role("button", name="Place order").click()
        time.sleep(1.5)
        page.screenshot(path=str(SHOTS / "15-store-order-confirmed.png"))
        page.goto("http://127.0.0.1:18080/#/search?q=wireless")
        time.sleep(1.5)
        page.screenshot(path=str(SHOTS / "16-store-search.png"))
        page.goto("http://127.0.0.1:18080/#/deals")
        time.sleep(1.5)
        page.screenshot(path=str(SHOTS / "17-store-deals.png"), full_page=True)
        browser.close()

    stop_sampling.set()
    final = get(f"{DASH}/api/dashboard")
    tech = get(f"{DASH}/api/technical")
    (OUT / "samples.json").write_text(json.dumps(samples, indent=1))
    (OUT / "final-dashboard.json").write_text(json.dumps(final, indent=1))
    (OUT / "technical.json").write_text(json.dumps(tech, indent=1, default=str))
    (OUT / "console-errors.json").write_text(json.dumps(console_errors, indent=1))
    log(f"console errors: {len(console_errors)}")
    server.should_exit = True
    runtime.stop()
    for p in procs:
        p.terminate()
    cluster.shutdown()
    (OUT / "run.log").write_text("\n".join(LOG))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
