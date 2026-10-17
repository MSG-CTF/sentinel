param([ValidateSet('local', 'local-recovery')][string]$Profile = 'local-recovery')
$ErrorActionPreference = 'Stop'
$slaToolDirectory = Join-Path $PSScriptRoot '.tooling/k3d'
New-Item -ItemType Directory -Path $slaToolDirectory -Force | Out-Null
$slaK3d = Join-Path $slaToolDirectory 'k3d.exe'
if (!(Test-Path -LiteralPath $slaK3d)) {
    Invoke-WebRequest -Uri 'https://github.com/k3d-io/k3d/releases/download/v5.9.0/k3d-windows-amd64.exe' -OutFile $slaK3d
}
$slaExpected = '49d0b9c796f5b8ba6f58a1dd5469e8b7b34bf9b6ce9078633eedb2789680a034'
if ((Get-FileHash -LiteralPath $slaK3d -Algorithm SHA256).Hash.ToLowerInvariant() -ne $slaExpected) {
    throw 'k3d checksum mismatch; the binary was not executed'
}
& python (Join-Path $PSScriptRoot 'build_runtime.py')
if ($LASTEXITCODE -ne 0) { throw 'Pinned Runtime build failed' }
$slaDocker = @('--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
foreach ($slaImage in @(
    'ghcr.io/k3s-io/k3s@sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65',
    'ghcr.io/k3d-io/k3d-tools@sha256:c57abce697e66feeb826922718738fe186b47779312d49d4e51e6fb9b8a6f34d'
)) {
    & docker @slaDocker image inspect $slaImage --format '{{.Id}}' *> $null
    if ($LASTEXITCODE -ne 0) {
        & docker @slaDocker pull $slaImage
        if ($LASTEXITCODE -ne 0) { throw 'Official execution image pull failed' }
    }
}
& docker @slaDocker tag ghcr.io/k3s-io/k3s@sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65 ghcr.io/k3s-io/k3s:v1.35.5-k3s1
if ($LASTEXITCODE -ne 0) { throw 'K3s image tag binding failed' }
& docker @slaDocker tag ghcr.io/k3d-io/k3d-tools@sha256:c57abce697e66feeb826922718738fe186b47779312d49d4e51e6fb9b8a6f34d ghcr.io/k3d-io/k3d-tools:5.9.0
if ($LASTEXITCODE -ne 0) { throw 'k3d tools image tag binding failed' }
& python (Join-Path $PSScriptRoot 'execution_lab.py') cluster
if ($LASTEXITCODE -ne 0) { throw 'Local K3s startup failed' }
& python (Join-Path $PSScriptRoot 'verify_execution_capabilities.py')
if ($LASTEXITCODE -ne 0) { throw 'Local K3s isolation checks failed' }
$slaMonitorEnv = Join-Path $PSScriptRoot '.execution/monitor.env'
if (!(Test-Path -LiteralPath $slaMonitorEnv)) { New-Item -ItemType File -Path $slaMonitorEnv | Out-Null }
$slaCompose = @('compose', '--project-name', 'msg-sla-real-local',
    '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-f', (Join-Path $PSScriptRoot 'compose.assets.yaml'),
    '-f', (Join-Path $PSScriptRoot 'compose.execution.yaml'))
& docker @slaDocker @slaCompose up -d --build runtime-db runtime
if ($LASTEXITCODE -ne 0) { throw 'Runtime startup failed' }
& python (Join-Path $PSScriptRoot 'execution_lab.py') connect
if ($LASTEXITCODE -ne 0) { throw 'Runtime workload connection failed' }
& (Join-Path $PSScriptRoot 'Start-Monitor.ps1') -Profile $Profile
