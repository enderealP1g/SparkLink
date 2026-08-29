[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [double]$IntervalSeconds = 60,
    [string]$SecretPath,
    [string]$LogPath,
    [string]$ControlPlaneSshHost = 'sparklink-node-166',
    [int]$ControlPlaneForwardPort = 18080
)

$ErrorActionPreference = 'Stop'

$localAppData = [Environment]::GetFolderPath('LocalApplicationData')
if ([string]::IsNullOrWhiteSpace($localAppData)) {
    $localAppData = Join-Path $env:USERPROFILE 'AppData\Local'
}
if ([string]::IsNullOrWhiteSpace($SecretPath)) {
    $SecretPath = Join-Path $RepositoryRoot 'runtime\secrets\control-plane-admin-token.dpapi'
}
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $localAppData 'SparkLink\logs\collector.log'
}

try {
    $startupDirectory = Split-Path -Parent $LogPath
    New-Item -ItemType Directory -Path $startupDirectory -Force | Out-Null
    Add-Content -LiteralPath $LogPath -Value ("collector_launcher_start={0}" -f [DateTime]::UtcNow.ToString('o')) -Encoding UTF8
}
catch {
    # Startup logging must never prevent the collector from attempting to run.
}

function Read-ProtectedToken([string]$Path) {
    Add-Type -AssemblyName System.Security
    $protected = [Convert]::FromBase64String(([IO.File]::ReadAllText($Path)).Trim())
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    try {
        return [Text.Encoding]::UTF8.GetString($plain)
    }
    finally {
        [Array]::Clear($plain, 0, $plain.Length)
    }
}

$exitCode = 1
$tunnel = $null
$adminToken = $null

try {
    $collectorPath = Join-Path $RepositoryRoot 'src\sparklink_xray_collector.py'
    if (-not (Test-Path -LiteralPath $collectorPath -PathType Leaf)) {
        throw 'collector source is missing'
    }
    if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) {
        throw 'protected collector secret is missing; provision it outside Git'
    }
    $python = Get-Command python.exe -ErrorAction Stop
    $ssh = Get-Command ssh.exe -ErrorAction Stop
    $logDirectory = Split-Path -Parent $LogPath
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $adminToken = Read-ProtectedToken $SecretPath
    if ([string]::IsNullOrWhiteSpace($adminToken)) {
        throw 'protected collector secret is empty'
    }
    $existingListener = Get-NetTCPConnection -LocalPort $ControlPlaneForwardPort -State Listen -ErrorAction SilentlyContinue
    if ($existingListener) {
        throw 'control-plane forward port is already in use'
    }
    $tunnelArgs = @(
        '-N', '-T',
        '-o', 'BatchMode=yes',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3',
        '-L', ("127.0.0.1:{0}:127.0.0.1:8080" -f $ControlPlaneForwardPort),
        $ControlPlaneSshHost
    )
    $tunnel = Start-Process -FilePath $ssh.Source -ArgumentList $tunnelArgs -WindowStyle Hidden -PassThru
    $tunnelReady = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        if ($tunnel.HasExited) {
            throw 'control-plane SSH tunnel exited before becoming ready'
        }
        if (Test-NetConnection -ComputerName 127.0.0.1 -Port $ControlPlaneForwardPort -InformationLevel Quiet) {
            $tunnelReady = $true
            break
        }
    }
    if (-not $tunnelReady) {
        throw 'control-plane SSH tunnel did not become ready'
    }
    $env:SPARKLINK_ADMIN_TOKEN = $adminToken
    & $python.Source $collectorPath --config (Join-Path $RepositoryRoot 'config\sparklink.example.json') `
        --endpoint ("http://127.0.0.1:{0}" -f $ControlPlaneForwardPort) `
        --interval-seconds $IntervalSeconds 2>&1 | ForEach-Object {
            Add-Content -LiteralPath $LogPath -Value ([string]$_) -Encoding UTF8
            Write-Output $_
        }
    $exitCode = $LASTEXITCODE
}
catch {
    try {
        $logDirectory = Split-Path -Parent $LogPath
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $line = [string]$_.InvocationInfo.Line
        $message = [string]$_.Exception.Message
        Add-Content -LiteralPath $LogPath -Value ("collector_launcher_error={0};line={1};message={2}" -f $_.Exception.GetType().Name, $line.Trim(), $message.Trim()) -Encoding UTF8
    }
    catch {
        # Logging must never mask the original launcher failure.
    }
    $exitCode = 1
}
finally {
    $env:SPARKLINK_ADMIN_TOKEN = $null
    $adminToken = $null
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-Process -Id $tunnel.Id -Force
        $tunnel.WaitForExit()
    }
}

exit $exitCode
