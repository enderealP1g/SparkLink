[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = 'SparkLink-Metering-Collector',
    [string]$ControlPlaneSshHost = 'sparklink-node-166',
    [int]$ControlPlaneForwardPort = 18080,
    [double]$IntervalSeconds = 60,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$secretPath = Join-Path $RepositoryRoot 'runtime\secrets\control-plane-admin-token.dpapi'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}

if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
    throw 'protected collector secret is missing; provision it outside Git before installation'
}

$collectorPath = Join-Path $RepositoryRoot 'src\sparklink_xray_collector.py'
if (-not (Test-Path -LiteralPath $collectorPath -PathType Leaf)) {
    throw 'collector source is missing'
}

$python = (Get-Command python.exe -ErrorAction Stop).Source
$configPath = Join-Path $RepositoryRoot 'config\sparklink.example.json'
$logPath = Join-Path $env:LOCALAPPDATA 'SparkLink\logs\collector.log'
$actionArguments = '"{0}" --config "{1}" --secret-path "{2}" --control-plane-ssh-host "{3}" --control-plane-forward-port {4} --interval-seconds {5} --log-path "{6}"' -f `
    $collectorPath, $configPath, $secretPath, $ControlPlaneSshHost, $ControlPlaneForwardPort, $IntervalSeconds, $logPath
$action = New-ScheduledTaskAction -Execute $python -Argument $actionArguments -WorkingDirectory $RepositoryRoot
$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description 'SparkLink management-plane metering collector; no product scheduling' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output ("registered and started {0}" -f $TaskName)
