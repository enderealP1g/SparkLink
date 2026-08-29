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

$exitCode = 1
$tunnel = $null

function Remove-StaleCollectorTunnel([int]$Port, [string]$SshHost) {
    $needle = "-L 127.0.0.1:{0}:127.0.0.1:8080 {1}" -f $Port, $SshHost
    $processes = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'ssh.exe' -and $_.CommandLine -and $_.CommandLine.Contains($needle)
    }
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($processes) {
        Start-Sleep -Milliseconds 300
    }
}

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
    Remove-StaleCollectorTunnel $ControlPlaneForwardPort $ControlPlaneSshHost
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
    & $python.Source $collectorPath --config (Join-Path $RepositoryRoot 'config\sparklink.example.json') `
        --secret-path $SecretPath `
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
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-Process -Id $tunnel.Id -Force
        $tunnel.WaitForExit()
    }
}

exit $exitCode
