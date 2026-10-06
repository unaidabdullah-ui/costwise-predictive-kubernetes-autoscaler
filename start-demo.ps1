<#
.SYNOPSIS
  One-click start of the CostWise demo on Windows 11 (Minikube, Docker driver).

.DESCRIPTION
  [1/8] checks prerequisites           [5/8] applies the Kubernetes manifests
  [2/8] starts Minikube (if needed)    [6/8] waits until the pods are ready
  [3/8] prepares the Python env        [7/8] creates the least-privilege credentials + port-forwards
  [4/8] builds the images in Minikube  [8/8] starts the CostWise control plane and opens the dashboard

  Safe to run again at any time. It never deletes your Minikube cluster.

.EXAMPLE
  .\start-demo.ps1
  .\start-demo.ps1 -Cluster costwise -Rebuild
  .\start-demo.ps1 -WithGrafana
#>
[CmdletBinding()]
param(
    [string]$Cluster = "minikube",     # Minikube profile = kubectl context name
    [int]$MemoryMB = 2200,             # only used when the cluster is created the first time
    [int]$Cpus = 2,
    [switch]$Rebuild,                  # rebuild the marketplace image even if it exists
    [switch]$WithGrafana,              # optional Grafana (+~150 MB RAM)
    [switch]$NoBrowser,
    [switch]$SkipPip                   # skip the Python dependency check
)

. "$PSScriptRoot\scripts\lib\costwise.ps1"
$Total = 8
Set-Location $script:ProjectRoot
Initialize-StateDir
Write-Host "COSTWISE - Predictive & Cost-Aware Kubernetes Autoscaling" -ForegroundColor White
Write-Host "Project: $script:ProjectRoot" -ForegroundColor Gray

# ----------------------------------------------------------------------------- 1
Write-Step 1 $Total "Checking prerequisites"
$missing = @()
foreach ($tool in @("docker", "minikube", "kubectl")) {
    if (Test-Command $tool) { Write-Ok "$tool found" } else { $missing += $tool; Write-Fail "$tool not found" }
}
$py = $null
foreach ($cand in @("py", "python")) {
    if (Test-Command $cand) {
        $v = Invoke-Native $cand @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
        if ($v.ExitCode -eq 0 -and [version]$v.Output.Trim() -ge [version]"3.10") { $py = $cand; Write-Ok "Python $($v.Output.Trim()) ($cand)"; break }
    }
}
if (-not $py -and -not (Get-PythonExe)) { $missing += "python"; Write-Fail "Python 3.10+ not found" "Install Python 3.11/3.12 from python.org (tick 'Add to PATH')." }
if ($missing.Count -gt 0) {
    Write-Fail "Missing: $($missing -join ', ')" "See README.md section 'Installation' (Docker Desktop, Minikube, kubectl, Python)."
    exit 1
}
$dockerInfo = Invoke-Native "docker" @("info", "--format", "{{.ServerVersion}}")
if ($dockerInfo.ExitCode -ne 0) { Write-Fail "Docker is not running" "Start Docker Desktop and wait until it says 'Engine running'."; exit 1 }
Write-Ok "Docker engine $($dockerInfo.Output.Trim())"
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $totalGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    $freeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    Write-Info "RAM: $totalGB GB total, $freeGB GB free"
    if ($freeGB -lt 1.5) { Write-Warn "Less than 1.5 GB free - close browsers/IDEs for a smoother demo." }
} catch { }

# ----------------------------------------------------------------------------- 2
Write-Step 2 $Total "Starting Minikube (profile '$Cluster')"
$status = Invoke-Native "minikube" @("status", "-p", $Cluster, "--format", "{{.Host}}/{{.APIServer}}")
if ($status.Output -match "Running/Running") {
    Write-Ok "Minikube is already running"
} else {
    Write-Info "Starting Minikube (Docker driver, $MemoryMB MB, $Cpus CPUs) - first start takes a few minutes ..."
    $start = Invoke-Native "minikube" @("start", "-p", $Cluster, "--driver=docker", "--memory=$MemoryMB", "--cpus=$Cpus")
    if ($start.ExitCode -ne 0) {
        Write-Host $start.Output
        Write-Fail "Minikube did not start" "Check Docker Desktop resources; then run .\start-demo.ps1 again. Your cluster is never deleted by this script."
        exit 1
    }
    Write-Ok "Minikube started"
}
$ctx = Invoke-Kubectl $Cluster @("get", "nodes", "-o", "name")
if ($ctx.ExitCode -ne 0) { Write-Fail "kubectl cannot reach the '$Cluster' context" "Run: minikube -p $Cluster update-context"; exit 1 }
Write-Ok "kubectl context '$Cluster' reachable"

# ----------------------------------------------------------------------------- 3
Write-Step 3 $Total "Preparing the Python environment (.venv)"
$venvPy = Get-PythonExe
if (-not $venvPy) {
    Write-Info "Creating .venv ..."
    $mk = Invoke-Native $py @("-m", "venv", ".venv")
    if ($mk.ExitCode -ne 0) { Write-Host $mk.Output; Write-Fail "Could not create .venv"; exit 1 }
    $venvPy = Get-PythonExe
}
if (-not $SkipPip) {
    $check = Invoke-Native $venvPy @("-c", "import numpy, requests, starlette, uvicorn, kubernetes")
    if ($check.ExitCode -ne 0) {
        Write-Info "Installing control-plane dependencies (numpy, requests, starlette, uvicorn, kubernetes) ..."
        $pip = Invoke-Native $venvPy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements\control-plane.txt")
        if ($pip.ExitCode -ne 0) { Write-Host $pip.Output; Write-Fail "pip install failed" "Check your internet connection / proxy, then re-run."; exit 1 }
    }
}
Write-Ok "Python environment ready ($venvPy)"
$modelOk = Test-Path (Join-Path $script:ProjectRoot "ml\models\lstm_lite.npz")
if ($modelOk) { Write-Ok "LSTM model present (ml\models\lstm_lite.npz)" }
else { Write-Warn "LSTM model missing - training it now (about 10-60 s) ..."; $null = Invoke-Native $venvPy @("-m", "ml.src.lite", "train", "--no-history") }

# ----------------------------------------------------------------------------- 4
Write-Step 4 $Total "Building the marketplace image inside Minikube"
$images = Invoke-Native "minikube" @("-p", $Cluster, "image", "ls")
if ($Rebuild -or ($images.Output -notmatch [regex]::Escape($script:ImageTag))) {
    Write-Info "minikube image build -t $($script:ImageTag) (1-3 minutes the first time) ..."
    $build = Invoke-Native "minikube" @("-p", $Cluster, "image", "build", "-t", $script:ImageTag, "-f", "docker/marketplace/Dockerfile", ".")
    if ($build.ExitCode -ne 0) {
        Write-Host $build.Output
        Write-Fail "Image build failed" "The first build downloads python:3.12-slim and pip packages (internet needed once)."
        exit 1
    }
    Write-Ok "Built $($script:ImageTag) (imagePullPolicy: Never - no registry needed)"
} else {
    Write-Ok "Image $($script:ImageTag) already in Minikube (use -Rebuild after code changes)"
}

# ----------------------------------------------------------------------------- 5
Write-Step 5 $Total "Applying Kubernetes manifests (namespace costwise)"
foreach ($f in @("kubernetes/namespace.yaml", "kubernetes/service-account.yaml", "kubernetes/rbac.yaml")) {
    $r = Invoke-Kubectl $Cluster @("apply", "-f", $f)
    if ($r.ExitCode -ne 0) { Write-Host $r.Output; Write-Fail "kubectl apply $f failed"; exit 1 }
}
Write-Ok "Namespace, ServiceAccount and least-privilege Role applied"
# traffic-generator control token: created once, random, never committed
$tokenFile = Join-Path $script:StateDir "control-token"
$existing = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "get", "secret", "costwise-secrets", "-o", "jsonpath={.data.control-token}")
if ($existing.ExitCode -eq 0 -and $existing.Output.Trim()) {
    $tok = [Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($existing.Output.Trim()))
    Write-PrivateFile $tokenFile $tok
    Write-Ok "Secret costwise-secrets exists (token synced to .costwise\control-token)"
} else {
    Write-PrivateFile $tokenFile (New-RandomToken)
    $r = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "create", "secret", "generic", "costwise-secrets", "--from-file=control-token=$tokenFile")
    if ($r.ExitCode -ne 0) { Write-Host $r.Output; Write-Fail "Could not create the Secret"; exit 1 }
    Write-Ok "Secret costwise-secrets created with a random token"
}
$cm = Invoke-Native "kubectl" @("--context", $Cluster, "-n", $script:Namespace, "create", "configmap", "prometheus-config",
    "--from-file=prometheus.yml=prometheus/prometheus.yml", "--from-file=costwise-alerts.yml=prometheus/rules/costwise-alerts.yml",
    "--dry-run=client", "-o", "yaml")
$cmFile = Join-Path $script:StateDir "prometheus-config.yaml"
Set-Content -Path $cmFile -Value $cm.Output -Encoding utf8
$manifests = @("kubernetes/marketplace.yaml", "kubernetes/traffic-generator.yaml", "kubernetes/monitoring/prometheus.yaml", $cmFile)
if ($WithGrafana) { $manifests += "kubernetes/optional/grafana.yaml" }
foreach ($f in $manifests) {
    $r = Invoke-Kubectl $Cluster @("apply", "-f", $f)
    if ($r.ExitCode -ne 0) { Write-Host $r.Output; Write-Fail "kubectl apply $f failed"; exit 1 }
}
if ($Rebuild) {
    $null = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "rollout", "restart", "deployment/marketplace", "deployment/traffic-generator")
}
Write-Ok "Marketplace, traffic generator and Prometheus applied$(if ($WithGrafana) { ' (+ Grafana)' })"

# ----------------------------------------------------------------------------- 6
Write-Step 6 $Total "Waiting for the pods to become ready"
$deps = @("marketplace", "traffic-generator", "prometheus")
if ($WithGrafana) { $deps += "grafana" }
foreach ($d in $deps) {
    $r = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "rollout", "status", "deployment/$d", "--timeout=240s")
    if ($r.ExitCode -ne 0) {
        Write-Fail "$d is not ready" "kubectl -n costwise get pods ; kubectl -n costwise describe deployment $d"
        $pods = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "get", "pods", "-o", "wide")
        Write-Host $pods.Output
        if ($pods.Output -match "ErrImageNeverPull|ErrImagePull|ImagePullBackOff") {
            Write-Warn "Image not found inside Minikube: run .\start-demo.ps1 -Rebuild"
        }
        exit 1
    }
    Write-Ok "$d ready"
}

# ----------------------------------------------------------------------------- 7
Write-Step 7 $Total "Creating least-privilege credentials and port-forwards"
$tok = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "create", "token", "cost-aware-autoscaler", "--duration=12h")
if ($tok.ExitCode -ne 0) { Write-Host $tok.Output; Write-Fail "Could not create a ServiceAccount token" "Needs Kubernetes 1.24+ (Minikube default is fine)."; exit 1 }
$server = (Invoke-Kubectl $Cluster @("config", "view", "--minify", "--raw", "-o", "jsonpath={.clusters[0].cluster.server}")).Output.Trim()
$caData = (Invoke-Kubectl $Cluster @("config", "view", "--minify", "--raw", "-o", "jsonpath={.clusters[0].cluster.certificate-authority-data}")).Output.Trim()
if (-not $caData) {
    $caFile = (Invoke-Kubectl $Cluster @("config", "view", "--minify", "--raw", "-o", "jsonpath={.clusters[0].cluster.certificate-authority}")).Output.Trim()
    $caData = [Convert]::ToBase64String([IO.File]::ReadAllBytes($caFile))
}
$kubeconfig = @"
apiVersion: v1
kind: Config
clusters:
- name: costwise
  cluster:
    server: $server
    certificate-authority-data: $caData
users:
- name: cost-aware-autoscaler
  user:
    token: $($tok.Output.Trim())
contexts:
- name: costwise-autoscaler
  context:
    cluster: costwise
    user: cost-aware-autoscaler
    namespace: costwise
current-context: costwise-autoscaler
"@
$kubeconfigPath = Join-Path $script:StateDir "kubeconfig"
Write-PrivateFile $kubeconfigPath $kubeconfig
Write-Ok "12-hour token for ServiceAccount cost-aware-autoscaler written to .costwise\kubeconfig (not the admin user)"

foreach ($name in @("pf-prometheus", "pf-generator", "pf-marketplace", "pf-grafana")) { $null = Stop-Recorded $name }
Start-Sleep -Milliseconds 500
$forwards = @(
    @{ Name = "pf-prometheus"; Target = "svc/prometheus"; Ports = "9090:9090" },
    @{ Name = "pf-generator"; Target = "svc/traffic-generator"; Ports = "8089:8089" },
    @{ Name = "pf-marketplace"; Target = "svc/marketplace"; Ports = "8080:80" }
)
if ($WithGrafana) { $forwards += @{ Name = "pf-grafana"; Target = "svc/grafana"; Ports = "3000:3000" } }
$psExe = (Get-Process -Id $PID).Path
foreach ($f in $forwards) {
    $port = [int]($f.Ports.Split(":")[0])
    if (-not (Test-PortFree $port)) { Write-Warn "Port $port is busy - another program uses it (stop it or run .\stop-demo.ps1)." }
    $log = Join-Path $script:LogDir "$($f.Name).log"
    $p = Start-Process -FilePath $psExe -WindowStyle Hidden -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script:ProjectRoot\scripts\lib\port-forward-loop.ps1`"",
        "-Context", $Cluster, "-Target", $f.Target, "-Ports", $f.Ports, "-LogFile", "`"$log`"")
    Save-Pid $f.Name $p.Id
}
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and -not ((Test-Url "http://127.0.0.1:9090/-/ready") -and (Test-Url "http://127.0.0.1:8089/health") -and (Test-Url "http://127.0.0.1:8080/health"))) {
    Start-Sleep -Seconds 1
}
foreach ($u in @("http://127.0.0.1:9090/-/ready", "http://127.0.0.1:8089/health", "http://127.0.0.1:8080/health")) {
    if (Test-Url $u) { Write-Ok "$u" } else { Write-Warn "$u not answering yet (the supervisor keeps retrying)" }
}

# ----------------------------------------------------------------------------- 8
Write-Step 8 $Total "Starting the CostWise control plane (controller + dashboard)"
$null = Stop-Recorded "control-plane"
if (-not (Test-PortFree 8000)) { Write-Fail "Port 8000 is in use" "Close the other program or run .\stop-demo.ps1 first."; exit 1 }
$env:KUBECONFIG = $kubeconfigPath
$env:SCALER_BACKEND = "kubernetes"
$env:KUBERNETES_NAMESPACE = $script:Namespace
$env:PROMETHEUS_URL = "http://127.0.0.1:9090"
$env:GENERATOR_URL = "http://127.0.0.1:8089"
$env:MARKETPLACE_URL = "http://127.0.0.1:8080"
if ($WithGrafana) { $env:GRAFANA_URL = "http://127.0.0.1:3000" }
$env:PYTHONUTF8 = "1"
$cpLog = Join-Path $script:LogDir "control-plane.log"
$p = Start-Process -FilePath $venvPy -WindowStyle Hidden -PassThru -WorkingDirectory $script:ProjectRoot `
    -ArgumentList @("-m", "costwise", "--log-file", "`"$cpLog`"") `
    -RedirectStandardError (Join-Path $script:LogDir "control-plane.stderr.log") `
    -RedirectStandardOutput (Join-Path $script:LogDir "control-plane.stdout.log")
Save-Pid "control-plane" $p.Id
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline -and -not (Test-Url "$($script:DashboardUrl)/health")) {
    if ($p.HasExited) { break }
    Start-Sleep -Seconds 1
}
if (-not (Test-Url "$($script:DashboardUrl)/health")) {
    Write-Fail "The control plane did not start" "See .costwise\logs\control-plane.stderr.log"
    Get-Content (Join-Path $script:LogDir "control-plane.stderr.log") -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}
Write-Ok "Dashboard: $($script:DashboardUrl)"

Write-Host ""
Write-Host "CostWise is running." -ForegroundColor Green
Write-Host "  Dashboard    $($script:DashboardUrl)      (press START DEMO, then FLASH SALE)"
Write-Host "  Marketplace  http://127.0.0.1:8080"
Write-Host "  Prometheus   http://127.0.0.1:9090"
if ($WithGrafana) { Write-Host "  Grafana      http://127.0.0.1:3000" }
Write-Host "  Logs         .costwise\logs\"
Write-Host "  Stop         .\stop-demo.ps1      Reset   .\reset-demo.ps1"
if (-not $NoBrowser) { Start-Process $script:DashboardUrl }
