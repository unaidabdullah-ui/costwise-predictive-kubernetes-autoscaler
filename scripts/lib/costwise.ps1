# Shared helpers for start-demo.ps1 / stop-demo.ps1 / reset-demo.ps1 (Windows PowerShell 5.1+ and PowerShell 7).
# Dot-source it:  . "$PSScriptRoot\scripts\lib\costwise.ps1"

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:StateDir = Join-Path $script:ProjectRoot ".costwise"
$script:LogDir = Join-Path $script:StateDir "logs"
$script:PidDir = Join-Path $script:StateDir "pids"
$script:Namespace = "costwise"
$script:ImageTag = "costwise-marketplace:2.1.0"
$script:DashboardUrl = "http://127.0.0.1:8000"

function Initialize-StateDir {
    foreach ($d in @($script:StateDir, $script:LogDir, $script:PidDir)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
    }
}

function Write-Step([int]$Number, [int]$Total, [string]$Text) {
    Write-Host ""
    Write-Host ("[{0}/{1}] {2}" -f $Number, $Total, $Text) -ForegroundColor Cyan
}
function Write-Ok([string]$Text) { Write-Host "      OK  $Text" -ForegroundColor Green }
function Write-Info([string]$Text) { Write-Host "          $Text" -ForegroundColor Gray }
function Write-Warn([string]$Text) { Write-Host "      !!  $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text, [string]$Hint = "") {
    Write-Host "      XX  $Text" -ForegroundColor Red
    if ($Hint) { Write-Host "          Fix: $Hint" -ForegroundColor Yellow }
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# Run an external command, return its output (stdout+stderr) and exit code without throwing.
function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $File @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    return [pscustomobject]@{ Output = ($out -join "`n"); ExitCode = $code }
}

function Invoke-Kubectl {
    param([string]$Context, [string[]]$Arguments)
    return Invoke-Native "kubectl" (@("--context", $Context) + $Arguments)
}

function New-RandomToken([int]$Bytes = 32) {
    $buf = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buf)
    $rng.Dispose()
    return ([Convert]::ToBase64String($buf)).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

# Write a file that only the current user can read (best effort on Windows).
function Write-PrivateFile([string]$Path, [string]$Content) {
    Set-Content -Path $Path -Value $Content -NoNewline -Encoding ascii
    try {
        $acl = Get-Acl $Path
        $acl.SetAccessRuleProtection($true, $false)
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($me, "FullControl", "Allow")
        $acl.SetAccessRule($rule)
        Set-Acl -Path $Path -AclObject $acl
    } catch { }
}

function Get-PythonExe {
    $venvPy = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    return $null
}

function Save-Pid([string]$Name, [int]$ProcessId) {
    Set-Content -Path (Join-Path $script:PidDir "$Name.pid") -Value $ProcessId -Encoding ascii
}

# Stop a recorded background process and its whole process tree.
function Stop-Recorded([string]$Name) {
    $file = Join-Path $script:PidDir "$Name.pid"
    if (-not (Test-Path $file)) { return $false }
    $procId = (Get-Content $file -Raw).Trim()
    Remove-Item $file -Force -ErrorAction SilentlyContinue
    if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        $null = Invoke-Native "taskkill" @("/PID", $procId, "/T", "/F")
        return $true
    }
    return $false
}

function Test-Url([string]$Url, [int]$TimeoutSec = 3) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch { return $false }
}

function Test-PortFree([int]$Port) {
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch { return $false } finally { if ($listener) { $listener.Stop() } }
}

function Invoke-DashboardAction([string]$Path, [string]$Body = "{}") {
    return Invoke-RestMethod -Method Post -Uri ($script:DashboardUrl + $Path) -Body $Body `
        -ContentType "application/json" -Headers @{ "X-CostWise" = "1" } -TimeoutSec 20
}
