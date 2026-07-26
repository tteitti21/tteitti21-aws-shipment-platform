[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9_.-]+$")]
    [string]$ImageTag,
    [string]$ProjectName = "shipment-event-platform"
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path

docker build `
    --file (Join-Path $RootDir "docker/api.Dockerfile") `
    --tag "${ProjectName}-api:${ImageTag}" `
    $RootDir
if ($LASTEXITCODE -ne 0) { throw "API image build failed." }

docker build `
    --file (Join-Path $RootDir "docker/worker.Dockerfile") `
    --tag "${ProjectName}-worker:${ImageTag}" `
    $RootDir
if ($LASTEXITCODE -ne 0) { throw "Worker image build failed." }

