# Supervised `kubectl port-forward`: restarts automatically when the forwarded pod
# goes away (e.g. CostWise scales the marketplace down and removes that pod).
# Started hidden by start-demo.ps1; stopped by stop-demo.ps1 (process tree kill).
param(
    [Parameter(Mandatory = $true)][string]$Context,
    [Parameter(Mandatory = $true)][string]$Target,      # e.g. svc/marketplace
    [Parameter(Mandatory = $true)][string]$Ports,       # e.g. 8080:80
    [string]$Namespace = "costwise",
    [string]$LogFile = ""
)
$ErrorActionPreference = "Continue"
while ($true) {
    $stamp = Get-Date -Format "HH:mm:ss"
    if ($LogFile) { Add-Content -Path $LogFile -Value "$stamp starting port-forward $Target $Ports" }
    if ($LogFile) {
        & kubectl --context $Context -n $Namespace port-forward --address 127.0.0.1 $Target $Ports *>> $LogFile
    } else {
        & kubectl --context $Context -n $Namespace port-forward --address 127.0.0.1 $Target $Ports | Out-Null
    }
    Start-Sleep -Seconds 1
}
