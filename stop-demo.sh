#!/usr/bin/env bash
# Stop traffic, control plane and port-forwards. --stop-cluster also runs `minikube stop` (never deletes).
set -uo pipefail
cd "$(dirname "$0")"
PIDS=.costwise/pids; CLUSTER=minikube; STOP_CLUSTER=0
while [[ $# -gt 0 ]]; do case "$1" in --cluster) CLUSTER="$2"; shift 2;; --stop-cluster) STOP_CLUSTER=1; shift;; *) shift;; esac; done
curl -fsS -m 5 -X POST -H 'X-CostWise: 1' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8000/demo/stop >/dev/null 2>&1 && echo "traffic stopped"
for n in control-plane pf-prometheus pf-generator pf-marketplace pf-grafana; do
  f="$PIDS/$n.pid"; [[ -f "$f" ]] || continue; p="$(cat "$f")"; rm -f "$f"
  pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null && echo "stopped $n"
done
kubectl --context "$CLUSTER" -n costwise scale deployment/marketplace --replicas=1 >/dev/null 2>&1 && echo "marketplace scaled to 1 pod"
[[ $STOP_CLUSTER == 1 ]] && minikube stop -p "$CLUSTER"
echo "Done."
