# Sandbox end-to-end harness (validation only — not part of the demo)

Used to validate the faculty demo where Docker/Minikube were not available
(see docs/TESTING.md). Linux only; needs root (cgroup v1 `cpu` controller), Chromium via
Playwright (`pip install playwright`), and a Prometheus 2.x binary (`PROM_DIR`).

* `podsim.py` — Kubernetes stand-in: each "pod" is a real `python -m marketplace`
  process limited to 250m CPU with a CFS quota (cfs_quota_us=25000 / period 100000, the
  mechanism Kubernetes uses for CPU limits), readiness delay, a round-robin TCP proxy as
  the Service, and file-based service discovery for Prometheus.
* `e2e.py` — starts Prometheus, the traffic generator, the control plane (with the
  stand-in scaler) and clicks through START DEMO → FLASH SALE → presentation mode →
  STOP TRAFFIC → technical/architecture pages → RETRAIN MODEL → RESET DEMO → storefront,
  saving screenshots and a 2-second sample log to `out/`.

```bash
sudo PROM_DIR=/path/to/prometheus-2.53.2.linux-amd64 python3 tools/sandbox/e2e.py
```
