param(
    [ValidateSet('local', 'local-recovery', 'deployed')][string]$Profile = 'local',
    [string]$DockerHost = 'npipe:////./pipe/dockerDesktopLinuxEngine',
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.cutover/active.json')) {
    throw 'An unfinished local data cutover exists; run python local/data_cutover.py rollback first'
}
$slaCompose = Join-Path $PSScriptRoot 'compose.yaml'
$slaAssetCompose = Join-Path $PSScriptRoot 'compose.assets.yaml'
$slaOldProfile = $env:SLA_PROFILE
try {
    if ($Profile -eq 'local-recovery' -and $DockerHost -ne 'npipe:////./pipe/dockerDesktopLinuxEngine') {
        throw 'Recovery is restricted to local Docker Desktop'
    }
    $slaAgent = Join-Path $PSScriptRoot 'recovery_agent.py'
    $slaAgentState = Join-Path $PSScriptRoot '.recovery'
    if ($Profile -eq 'local-recovery' -or (Test-Path -LiteralPath (Join-Path $slaAgentState 'executor.lock'))) {
        $slaPython = (Get-Command python -ErrorAction Stop).Source
        & $slaPython $slaAgent stop
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the previous local executor' }
    }
    if ($Profile -eq 'local-recovery') {
        & $slaPython $slaAgent init
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize local executor credentials' }
    } else {
        New-Item -ItemType Directory -Path (Join-Path $slaAgentState 'secrets') -Force | Out-Null
    }
    $env:SLA_PROFILE = $Profile
    if ($Profile -in @('local', 'local-recovery')) {
        & python (Join-Path $PSScriptRoot 'prepare_assets.py')
        if ($LASTEXITCODE -ne 0) { throw 'Local asset source preparation failed' }
        $slaExecutionConnected = Test-Path -LiteralPath (Join-Path $PSScriptRoot '.execution/connection.json')
        if ($slaExecutionConnected) {
            if ($DockerHost -ne 'npipe:////./pipe/dockerDesktopLinuxEngine') {
                throw 'The registered execution lab is restricted to local Docker Desktop'
            }
            & python (Join-Path $PSScriptRoot 'execution_lab.py') cluster
            if ($LASTEXITCODE -ne 0) { throw 'Local execution cluster startup failed' }
            & python (Join-Path $PSScriptRoot 'execution_lab.py') monitor --profile $Profile
            if ($LASTEXITCODE -ne 0) { throw 'Local execution monitor configuration failed' }
        }
        $slaBackendPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../msg-backend'))
        if (!(Test-Path -LiteralPath (Join-Path $slaBackendPath 'manage.py'))) {
            throw 'Clone MSG-CTF/msg-backend beside sentinel before starting the local profile'
        }
        $slaArgs = @('--host', $DockerHost, 'compose', '--project-name', 'msg-sla-real-local', '-f', $slaCompose, '-f', $slaAssetCompose)
        if ($slaExecutionConnected) { $slaArgs += @('-f', (Join-Path $PSScriptRoot 'compose.execution.yaml')) }
        $slaArgs += @('up', '-d')
        if (!$SkipBuild) { $slaArgs += '--build' }
        & docker @slaArgs
        if ($LASTEXITCODE -ne 0) { throw 'Local service startup failed' }
        foreach ($slaBackend in @('backend-internal', 'backend-external')) {
            & docker --host $DockerHost compose --project-name msg-sla-real-local -f $slaCompose exec -T $slaBackend python manage.py migrate --noinput
            if ($LASTEXITCODE -ne 0) { throw "Migration failed for $slaBackend" }
        }
    } else {
        $slaArgs = @('--host', $DockerHost, 'compose', '--project-name', 'msg-sla-real-local', '-f', $slaCompose, 'up', '-d', '--no-deps')
        if (!$SkipBuild) { $slaArgs += '--build' }
        $slaArgs += 'sentinel'
        & docker @slaArgs
        if ($LASTEXITCODE -ne 0) { throw 'Monitor startup failed' }
    }
    Write-Host "Profile: $Profile"
    Write-Host 'Dashboard: http://127.0.0.1:58240/'
    if ($Profile -eq 'local-recovery') {
        Start-Process -FilePath $slaPython -ArgumentList @(('"' + $slaAgent + '"'), 'run') -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -RedirectStandardOutput (Join-Path $slaAgentState 'stdout.log') -RedirectStandardError (Join-Path $slaAgentState 'stderr.log') | Out-Null
        Write-Host 'Mode: automatic_local; fixed application services, stopped containers only'
    } else {
        Write-Host 'Mode: observe_only; no recovery requests are sent'
    }
} finally {
    $env:SLA_PROFILE = $slaOldProfile
}
