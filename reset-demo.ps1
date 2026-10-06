<#
.SYNOPSIS
  Reset the CostWise demo to a clean starting point (for the next presentation).

.DESCRIPTION
  Default: stops traffic, clears charts/timeline/cooldowns/cost counter and scales the
  marketplace back to the minimum (same as the RESET DEMO button).
  -ClearHistory  also deletes the measured traffic history used by RETRAIN MODEL.
  -RestoreModel  retrains the shipped bootstrap-only model (undoes RETRAIN MODEL).
  -RestartPods   restarts the marketplace and traffic-generator pods.
  Never deletes the Minikube cluster.
#>
[CmdletBinding()]
param(
    [string]$Cluster = "minikube",
    [switch]$ClearHistory,
    [switch]$RestoreModel,
    [switch]$RestartPods
)
. "$PSScriptRoot\scripts\lib\costwise.ps1"
Set-Location $script:ProjectRoot
Initialize-StateDir

Write-Host "Resetting the CostWise demo ..." -ForegroundColor Cyan
$dashboard = $false
try {
    $r = Invoke-DashboardAction "/demo/reset"
    $dashboard = $true
    Write-Ok "Demo reset through the dashboard ($($r.notes -join '; '))"
} catch {
    Write-Info "Dashboard not running - resetting Kubernetes directly"
    $r = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "scale", "deployment/marketplace", "--replicas=1")
    if ($r.ExitCode -eq 0) { Write-Ok "Marketplace scaled to 1 pod" } else { Write-Warn $r.Output }
}
if ($RestartPods) {
    $r = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "rollout", "restart", "deployment/marketplace", "deployment/traffic-generator")
    if ($r.ExitCode -eq 0) { Write-Ok "Pods restarting (traffic generator returns to 'stopped')" } else { Write-Warn $r.Output }
}
if ($ClearHistory) {
    $hist = Join-Path $script:StateDir "history.csv"
    if (Test-Path $hist) { Remove-Item $hist -Force; Write-Ok "Measured history deleted (.costwise\history.csv)" }
    if ($dashboard) { Write-Warn "Restart the control plane (.\start-demo.ps1) so it starts a new history file." }
}
if ($RestoreModel) {
    $py = Get-PythonExe
    if (-not $py) { Write-Fail "No .venv found" "Run .\start-demo.ps1 once first." ; exit 1 }
    $r = Invoke-Native $py @("-m", "ml.src.lite", "train", "--no-history")
    if ($r.ExitCode -eq 0) { Write-Ok "Bootstrap-only LSTM restored (ml\models\lstm_lite.npz)" } else { Write-Host $r.Output; exit 1 }
    if ($dashboard) { Write-Info "Restart the control plane (.\stop-demo.ps1 then .\start-demo.ps1) to load it." }
}
Write-Host "Ready for the next demo: open $($script:DashboardUrl) and press START DEMO." -ForegroundColor Green
