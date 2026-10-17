param([string]$DockerHost = 'npipe:////./pipe/dockerDesktopLinuxEngine')
$ErrorActionPreference = 'Stop'
if ((Test-Path -LiteralPath (Join-Path $PSScriptRoot '.execution/connection.json')) -and $DockerHost -ne 'npipe:////./pipe/dockerDesktopLinuxEngine') {
    throw 'The registered execution lab is restricted to local Docker Desktop'
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.recovery/executor.lock')) {
    & python (Join-Path $PSScriptRoot 'recovery_agent.py') stop
    if ($LASTEXITCODE -ne 0) { throw 'Local executor stop failed' }
}
$slaStopArgs = @('--host', $DockerHost, 'compose', '--project-name', 'msg-sla-real-local', '-f', (Join-Path $PSScriptRoot 'compose.yaml'))
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.assets/services.env')) {
    $slaStopArgs += @('-f', (Join-Path $PSScriptRoot 'compose.assets.yaml'))
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.execution/connection.json')) {
    $slaStopArgs += @('-f', (Join-Path $PSScriptRoot 'compose.execution.yaml'))
}
& docker @slaStopArgs stop
if ($LASTEXITCODE -ne 0) { throw 'Local project stop failed' }
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.execution/connection.json')) {
    & python (Join-Path $PSScriptRoot 'execution_lab.py') stop-cluster
    if ($LASTEXITCODE -ne 0) { throw 'Local execution cluster stop failed' }
}
Write-Host 'Local Sentinel services and any registered msg-sla-execution node were stopped; data volumes were kept'
