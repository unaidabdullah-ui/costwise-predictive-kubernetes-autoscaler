#!/usr/bin/env bash
# One-click start of the CostWise demo on Linux / macOS / WSL (same 8 steps as start-demo.ps1).
# Usage: ./start-demo.sh [--cluster minikube] [--rebuild] [--with-grafana] [--no-browser]
# Never deletes your Minikube cluster.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"; STATE="$ROOT/.costwise"; LOGS="$STATE/logs"; PIDS="$STATE/pids"
NS=costwise; IMAGE=costwise-marketplace:2.1.0; CLUSTER=minikube; REBUILD=0; GRAFANA=0; BROWSER=1
MEMORY=2200; CPUS=2
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster) CLUSTER="$2"; shift 2;;
    --rebuild) REBUILD=1; shift;;
    --with-grafana) GRAFANA=1; shift;;
    --no-browser) BROWSER=0; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
mkdir -p "$LOGS" "$PIDS"; chmod 700 "$STATE"
step() { printf '\n\033[36m[%s/8] %s\033[0m\n' "$1" "$2"; }
ok() { printf '      \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '      \033[33m!!\033[0m  %s\n' "$1"; }
fail() { printf '      \033[31mXX\033[0m  %s\n' "$1"; [[ -n "${2:-}" ]] && printf '          Fix: %s\n' "$2"; exit 1; }
kc() { kubectl --context "$CLUSTER" "$@"; }
stop_pid() { local f="$PIDS/$1.pid"; [[ -f "$f" ]] || return 0; local p; p="$(cat "$f")"; rm -f "$f";
  pkill -TERM -P "$p" 2>/dev/null || true; kill "$p" 2>/dev/null || true; }
url_ok() { curl -fsS -m 3 -o /dev/null "$1" 2>/dev/null; }

step 1 "Checking prerequisites"
for t in docker minikube kubectl curl; do command -v "$t" >/dev/null && ok "$t found" || fail "$t not found" "see README 'Installation'"; done
PY="$(command -v python3 || command -v python || true)"; [[ -n "$PY" ]] || fail "python3 not found"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Python 3.10+ required"
ok "$("$PY" --version)"
docker info >/dev/null 2>&1 || fail "Docker is not running" "start Docker"

step 2 "Starting Minikube (profile '$CLUSTER')"
if minikube status -p "$CLUSTER" --format '{{.Host}}/{{.APIServer}}' 2>/dev/null | grep -q "Running/Running"; then ok "already running"
else minikube start -p "$CLUSTER" --driver=docker --memory="$MEMORY" --cpus="$CPUS" || fail "Minikube did not start"; ok "started"; fi
kc get nodes -o name >/dev/null || fail "kubectl cannot reach context $CLUSTER" "minikube -p $CLUSTER update-context"

step 3 "Preparing the Python environment (.venv)"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
VPY="$ROOT/.venv/bin/python"
"$VPY" -c 'import numpy, requests, starlette, uvicorn, kubernetes' 2>/dev/null || \
  "$VPY" -m pip install -q --disable-pip-version-check -r requirements/control-plane.txt || fail "pip install failed"
ok "Python environment ready"
[[ -f ml/models/lstm_lite.npz ]] || { warn "LSTM model missing - training"; "$VPY" -m ml.src.lite train --no-history; }

step 4 "Building the marketplace image inside Minikube"
if [[ $REBUILD == 1 ]] || ! minikube -p "$CLUSTER" image ls | grep -q "$IMAGE"; then
  minikube -p "$CLUSTER" image build -t "$IMAGE" -f docker/marketplace/Dockerfile . || fail "image build failed"
  ok "built $IMAGE"
else ok "$IMAGE already present (--rebuild after code changes)"; fi

step 5 "Applying Kubernetes manifests"
kc apply -f kubernetes/namespace.yaml -f kubernetes/service-account.yaml -f kubernetes/rbac.yaml >/dev/null
TOKEN_FILE="$STATE/control-token"
if existing="$(kc -n $NS get secret costwise-secrets -o 'jsonpath={.data.control-token}' 2>/dev/null)" && [[ -n "$existing" ]]; then
  (umask 077; printf '%s' "$existing" | base64 -d > "$TOKEN_FILE"); ok "Secret exists (token synced)"
else
  (umask 077; "$PY" -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "$TOKEN_FILE")
  kc -n $NS create secret generic costwise-secrets --from-file=control-token="$TOKEN_FILE" >/dev/null; ok "Secret created"
fi
kc -n $NS create configmap prometheus-config --from-file=prometheus.yml=prometheus/prometheus.yml \
  --from-file=costwise-alerts.yml=prometheus/rules/costwise-alerts.yml --dry-run=client -o yaml | kc apply -f - >/dev/null
EXTRA=(); [[ $GRAFANA == 1 ]] && EXTRA=(-f kubernetes/optional/grafana.yaml)
kc apply -f kubernetes/marketplace.yaml -f kubernetes/traffic-generator.yaml -f kubernetes/monitoring/prometheus.yaml "${EXTRA[@]}" >/dev/null
[[ $REBUILD == 1 ]] && kc -n $NS rollout restart deployment/marketplace deployment/traffic-generator >/dev/null
ok "manifests applied"

step 6 "Waiting for the pods to become ready"
DEPS=(marketplace traffic-generator prometheus); [[ $GRAFANA == 1 ]] && DEPS+=(grafana)
for d in "${DEPS[@]}"; do
  kc -n $NS rollout status deployment/"$d" --timeout=240s >/dev/null || { kc -n $NS get pods; fail "$d not ready" "kubectl -n costwise describe deployment $d (ErrImageNeverPull? use --rebuild)"; }
  ok "$d ready"
done

step 7 "Creating least-privilege credentials and port-forwards"
SA_TOKEN="$(kc -n $NS create token cost-aware-autoscaler --duration=12h)"
SERVER="$(kc config view --minify --raw -o 'jsonpath={.clusters[0].cluster.server}')"
CA="$(kc config view --minify --raw -o 'jsonpath={.clusters[0].cluster.certificate-authority-data}')"
if [[ -z "$CA" ]]; then CA="$(base64 < "$(kc config view --minify --raw -o 'jsonpath={.clusters[0].cluster.certificate-authority}')" | tr -d '\n')"; fi
(umask 077; cat > "$STATE/kubeconfig" <<KCFG
apiVersion: v1
kind: Config
clusters:
- name: costwise
  cluster: {server: "$SERVER", certificate-authority-data: "$CA"}
users:
- name: cost-aware-autoscaler
  user: {token: "$SA_TOKEN"}
contexts:
- name: costwise-autoscaler
  context: {cluster: costwise, user: cost-aware-autoscaler, namespace: costwise}
current-context: costwise-autoscaler
KCFG
)
ok "12 h ServiceAccount token written to .costwise/kubeconfig"
for n in pf-prometheus pf-generator pf-marketplace pf-grafana; do stop_pid "$n"; done
forward() { # name target ports
  ( while true; do kubectl --context "$CLUSTER" -n $NS port-forward --address 127.0.0.1 "$2" "$3" >>"$LOGS/$1.log" 2>&1; sleep 1; done ) &
  echo $! > "$PIDS/$1.pid"; }
forward pf-prometheus svc/prometheus 9090:9090
forward pf-generator svc/traffic-generator 8089:8089
forward pf-marketplace svc/marketplace 8080:80
[[ $GRAFANA == 1 ]] && forward pf-grafana svc/grafana 3000:3000
for i in $(seq 30); do url_ok http://127.0.0.1:9090/-/ready && url_ok http://127.0.0.1:8089/health && url_ok http://127.0.0.1:8080/health && break; sleep 1; done
ok "port-forwards supervised (they restart automatically)"

step 8 "Starting the CostWise control plane"
stop_pid control-plane
export KUBECONFIG="$STATE/kubeconfig" SCALER_BACKEND=kubernetes KUBERNETES_NAMESPACE=$NS \
  PROMETHEUS_URL=http://127.0.0.1:9090 GENERATOR_URL=http://127.0.0.1:8089 MARKETPLACE_URL=http://127.0.0.1:8080
[[ $GRAFANA == 1 ]] && export GRAFANA_URL=http://127.0.0.1:3000
nohup "$VPY" -m costwise --log-file "$LOGS/control-plane.log" >"$LOGS/control-plane.stdout.log" 2>"$LOGS/control-plane.stderr.log" &
echo $! > "$PIDS/control-plane.pid"
for i in $(seq 45); do url_ok http://127.0.0.1:8000/health && break; sleep 1; done
url_ok http://127.0.0.1:8000/health || { tail -20 "$LOGS/control-plane.stderr.log"; fail "control plane did not start"; }
ok "Dashboard: http://127.0.0.1:8000"
echo; echo "CostWise is running. Press START DEMO, then FLASH SALE.  Stop: ./stop-demo.sh  Reset: ./reset-demo.sh"
if [[ $BROWSER == 1 ]]; then (xdg-open http://127.0.0.1:8000 || open http://127.0.0.1:8000) >/dev/null 2>&1 || true; fi
