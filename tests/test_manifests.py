"""Static checks of the Kubernetes manifests, Prometheus config and scripts (no cluster needed)."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "kubernetes"


def docs(path):
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


def all_docs():
    out = []
    for f in sorted(K8S.rglob("*.yaml")):
        if f.name != "kustomization.yaml":
            out += [(f, d) for d in docs(f.relative_to(ROOT))]
    return out


def test_controller_rbac_is_least_privilege():
    role, binding = docs("kubernetes/rbac.yaml")
    assert role["kind"] == "Role" and role["metadata"]["namespace"] == "costwise"
    rules = {(tuple(r["apiGroups"]), tuple(r["resources"])): set(r["verbs"]) for r in role["rules"]}
    assert rules[(("apps",), ("deployments",))] == {"get", "list", "watch"}
    assert rules[(("apps",), ("deployments/scale",))] == {"get", "patch", "update"}
    assert rules[(("",), ("pods", "events"))] == {"get", "list", "watch"}
    for (_, resources), verbs in rules.items():
        assert "*" not in verbs and "secrets" not in resources and "delete" not in verbs and "create" not in verbs
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"][0]["name"] == "cost-aware-autoscaler"


def test_no_cluster_wide_permissions_anywhere():
    kinds = {d["kind"] for _, d in all_docs()}
    assert "ClusterRole" not in kinds and "ClusterRoleBinding" not in kinds
    for f, d in all_docs():
        if d["kind"] == "RoleBinding":
            assert d["roleRef"]["kind"] == "Role" and d["roleRef"]["name"] != "cluster-admin", f


def test_no_secrets_are_committed():
    for f, d in all_docs():
        assert d["kind"] != "Secret", f
    marker = "BEGIN " + "PRIVATE KEY"
    for p in ROOT.rglob("*"):
        if p.is_file() and "tests" not in p.parts and p.suffix in (".yaml", ".yml", ".ps1", ".sh", ".py", ".example"):
            assert marker not in p.read_text(errors="ignore"), p


def test_pods_are_hardened_for_restricted_psa():
    ns = docs("kubernetes/namespace.yaml")[0]
    assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    for f, d in all_docs():
        if d["kind"] != "Deployment":
            continue
        spec = d["spec"]["template"]["spec"]
        assert spec["securityContext"]["runAsNonRoot"] is True, f
        assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault", f
        for c in spec["containers"]:
            sc = c["securityContext"]
            assert sc["allowPrivilegeEscalation"] is False and sc["capabilities"]["drop"] == ["ALL"], f
            assert "limits" in c["resources"] and "requests" in c["resources"], f


def test_local_images_never_pulled():
    for f, d in all_docs():
        if d["kind"] == "Deployment":
            for c in d["spec"]["template"]["spec"]["containers"]:
                if c["image"].startswith("costwise-"):
                    assert c["imagePullPolicy"] == "Never", f


def test_marketplace_matches_autoscaler_assumptions():
    from autoscaler.config import AutoscalerConfig

    dep, svc = docs("kubernetes/marketplace.yaml")
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert c["resources"]["limits"]["cpu"] == "250m"
    assert dep["spec"]["selector"]["matchLabels"]["app"] == "marketplace"
    cfg = AutoscalerConfig()
    assert dep["metadata"]["name"] == cfg.target_deployment
    assert dep["metadata"]["namespace"] == cfg.kubernetes_namespace
    assert svc["spec"]["ports"][0]["port"] == 80


def test_prometheus_scrapes_the_jobs_the_queries_use():
    from autoscaler.config import DEFAULT_CPU_QUERY

    prom = yaml.safe_load((ROOT / "prometheus/prometheus.yml").read_text())
    jobs = {j["job_name"] for j in prom["scrape_configs"]}
    assert {"marketplace", "traffic-generator"} <= jobs
    assert 'job="marketplace"' in DEFAULT_CPU_QUERY
    assert prom["global"]["scrape_interval"] == "5s"
    assert prom["rule_files"] == ["/etc/prometheus/costwise-alerts.yml"]


def test_hpa_baseline_targets_the_same_load():
    hpa = docs("kubernetes/experiments/hpa-baseline.yaml")[0]
    metric = hpa["spec"]["metrics"][0]["resource"]
    assert metric["target"]["averageValue"] == "175m"  # 70 % of the 250m limit
    assert hpa["spec"]["maxReplicas"] == 8


def test_scripts_follow_the_documented_contract():
    start = (ROOT / "start-demo.ps1").read_text()
    for n in range(1, 9):
        assert f"Write-Step {n} $Total" in start
    assert "[string]$Cluster" in start and "-Profile" not in start
    for script in ("start-demo.ps1", "stop-demo.ps1", "reset-demo.ps1", "start-demo.sh", "stop-demo.sh",
                   "reset-demo.sh"):
        text = (ROOT / script).read_text()
        assert "minikube delete" not in text, script
    assert "port-forward-loop.ps1" in start and "create\", \"token\"" in start
