<#
.SYNOPSIS
  Stop the CostWise demo: traffic, control plane and port-forwards.

.DESCRIPTION
  By default Minikube keeps running (next start is fast) and nothing is deleted.
  -StopCluster also runs `minikube stop` to free RAM/CPU (the cluster is kept, never deleted).

.EXAMPLE
  .\stop-demo.ps1
  .\stop-demo.ps1 -StopCluster
#>
[CmdletBinding()]
param(
    [string]$Cluster = "minikube",
    [switch]$StopCluster
)
. "$PSScriptRoot\scripts\lib\costwise.ps1"
Set-Location $script:ProjectRoot
Initialize-StateDir

Write-Host "Stopping CostWise ..." -ForegroundColor Cyan
try { $null = Invoke-DashboardAction "/demo/stop"; Write-Ok "Traffic stopped" } catch { Write-Info "Dashboard not running (traffic not changed)" }
if (Stop-Recorded "control-plane") { Write-Ok "Control plane stopped" } else { Write-Info "Control plane was not running" }
foreach ($name in @("pf-prometheus", "pf-generator", "pf-marketplace", "pf-grafana")) {
    if (Stop-Recorded $name) { Write-Ok "Port-forward $name stopped" }
}
if (Test-Command "kubectl") {
    $r = Invoke-Kubectl $Cluster @("-n", $script:Namespace, "scale", "deployment/marketplace", "--replicas=1")
    if ($r.ExitCode -eq 0) { Write-Ok "Marketplace scaled back to 1 pod" }
}
if ($StopCluster) {
    $r = Invoke-Native "minikube" @("stop", "-p", $Cluster)
    if ($r.ExitCode -eq 0) { Write-Ok "Minikube stopped (cluster kept; .\start-demo.ps1 starts it again)" }
    else { Write-Warn "minikube stop failed: $($r.Output)" }
}
Write-Host "Done. Start again with .\start-demo.ps1" -ForegroundColor Green
