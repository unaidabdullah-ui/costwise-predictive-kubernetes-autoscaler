# Troubleshooting

Logs: `.costwise\logs\` (control-plane.log, control-plane.stderr.log, pf-*.log).
The dashboard's status pills show the failing component and the fix in their tooltip.

| Symptom | Cause | Fix |
|---|---|---|
| `running scripts is disabled on this system` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| [1/8] "Docker is not running" | Docker Desktop stopped | start it, wait for "Engine running" |
| [2/8] Minikube does not start | too little memory / old cluster | close apps; `minikube start -p minikube --memory=2200`; as a **last resort and only if you accept losing it** you may delete the cluster yourself — the scripts never do |
| [4/8] build fails | no internet on first build | connect once (python base image + pip packages), then it is cached |
| pod `ErrImageNeverPull` | image not inside this Minikube profile | `.\start-demo.ps1 -Rebuild` (and the same `-Cluster` you started with) |
| pod `Pending` | not enough CPU/RAM in Minikube | `kubectl -n costwise describe pod <name>`; stop Grafana; reduce `MAX_REPLICAS` |
| Prometheus pill red | port-forward 9090 down or Prometheus restarting | wait 5 s (auto-restart); else re-run start-demo |
| "no marketplace data yet" | Prometheus scrapes every 5 s; rate needs 30 s | wait ~30 s after START DEMO |
| Kubernetes pill: "expired or invalid credentials" | 12-hour token expired | re-run `.\start-demo.ps1` |
| Kubernetes pill: "permission denied by RBAC" | Role not applied | re-run start-demo (applies `kubernetes/rbac.yaml`) |
| Traffic pill: "rejected the control token" | Secret recreated | re-run start-demo (syncs `.costwise\control-token`) |
| Port 8000/8080/8089/9090 busy | old processes | `.\stop-demo.ps1`; or find it: `netstat -ano | findstr :8000` |
| "Safe mode: warming up" | < 30 s of history | expected right after START DEMO |
| "Safe mode: forecast confidence …" | traffic unlike anything the model saw | it recovers automatically; RETRAIN MODEL after a few demos |
| Pods flap up and down | thresholds near a boundary | raise `STABILIZATION_WINDOW_SECONDS` or `CHANGE_PENALTY` |
| Latency high even with many pods | laptop CPU saturated (one node) | close other apps; lower `MARKETPLACE_WORK_SCALE` in `kubernetes/marketplace.yaml` and `CAPACITY_PER_POD` together |
| Dashboard shows "DRY RUN" | started with `--dry-run` or `SCALER_BACKEND=dry-run` | start via start-demo |
| Laptop slow after the demo | Minikube keeps running | `.\stop-demo.ps1 -StopCluster` |
