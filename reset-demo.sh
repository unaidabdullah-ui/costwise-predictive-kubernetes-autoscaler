#!/usr/bin/env bash
# Reset the demo (same as the RESET DEMO button). --clear-history deletes measured history,
# --restore-model retrains the shipped bootstrap-only model. Never deletes the cluster.
set -uo pipefail
cd "$(dirname "$0")"
CLUSTER=minikube; CLEAR=0; RESTORE=0
while [[ $# -gt 0 ]]; do case "$1" in --cluster) CLUSTER="$2"; shift 2;; --clear-history) CLEAR=1; shift;; --restore-model) RESTORE=1; shift;; *) shift;; esac; done
if curl -fsS -m 20 -X POST -H 'X-CostWise: 1' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8000/demo/reset; then echo; echo "demo reset"
else kubectl --context "$CLUSTER" -n costwise scale deployment/marketplace --replicas=1 && echo "marketplace scaled to 1 pod"; fi
[[ $CLEAR == 1 ]] && rm -f .costwise/history.csv && echo "measured history deleted"
if [[ $RESTORE == 1 ]]; then PY=.venv/bin/python; [[ -x $PY ]] || PY=python3; "$PY" -m ml.src.lite train --no-history && echo "bootstrap-only model restored (restart the control plane to load it)"; fi
