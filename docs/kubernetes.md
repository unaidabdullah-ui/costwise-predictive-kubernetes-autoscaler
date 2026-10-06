# Kubernetes setup

| File | Contents |
|---|---|
| `kubernetes/namespace.yaml` | Namespace `costwise`, Pod Security Admission `restricted` |
| `kubernetes/service-account.yaml` | ServiceAccount `cost-aware-autoscaler` (token not auto-mounted) |
| `kubernetes/rbac.yaml` | Role + RoleBinding (least privilege, see below) |
| `kubernetes/marketplace.yaml` | Deployment (1 replica initially, 100m/250m CPU, 96/192 MiB, probes, hardened) + Service :80 |
| `kubernetes/traffic-generator.yaml` | Deployment + Service :8089; `CONTROL_TOKEN` from Secret `costwise-secrets` |
| `kubernetes/monitoring/prometheus.yaml` | Prometheus SA + discovery Role, Deployment (2 h retention, emptyDir), Service :9090 |
| `kubernetes/optional/grafana.yaml` | Grafana (anonymous viewer) + provisioning |
| `kubernetes/optional/control-plane.yaml` | In-cluster control plane (alternative to the laptop) |
| `kubernetes/experiments/hpa-baseline.yaml` | HPA used only by the experiments (method B) |
| `kubernetes/kustomization.yaml` | Core resources for `kubectl apply -k` (create the Secret and Prometheus ConfigMap first) |

Created by start-demo (not committed): Secret `costwise-secrets` (random token) and
ConfigMap `prometheus-config` (from `prometheus/prometheus.yml` and `prometheus/rules/`).

## RBAC (least privilege)

```yaml
- apiGroups: ["apps"]  resources: ["deployments"]        verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]  resources: ["deployments/scale"]  verbs: ["get", "patch", "update"]
- apiGroups: [""]      resources: ["pods", "events"]     verbs: ["get", "list", "watch"]
```

Namespace-scoped. No ClusterRole, no cluster-admin, no Secrets, no create/delete. The
only write is the replica count. Check it yourself:

```powershell
kubectl auth can-i patch deployments/scale -n costwise --as=system:serviceaccount:costwise:cost-aware-autoscaler   # yes
kubectl auth can-i delete pods -n costwise --as=system:serviceaccount:costwise:cost-aware-autoscaler              # no
kubectl auth can-i get secrets -n costwise --as=system:serviceaccount:costwise:cost-aware-autoscaler              # no
kubectl auth can-i list deployments -n default --as=system:serviceaccount:costwise:cost-aware-autoscaler          # no
```

## Images

`costwise-marketplace:2.1.0` (store + generator, ~60 MB of Python packages) is built
inside Minikube with `minikube image build`, so `imagePullPolicy: Never` works and there
is no registry and no `ErrImagePull`. Prometheus (`prom/prometheus:v2.53.0`) and the
optional Grafana are pulled from Docker Hub on first start.

## Useful commands

```powershell
kubectl -n costwise get pods -w
kubectl -n costwise get deployment marketplace
kubectl -n costwise logs deploy/traffic-generator
kubectl -n costwise describe pod <name>
minikube -p minikube image ls | findstr costwise
```
