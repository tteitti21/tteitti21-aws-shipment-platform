[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Push-Location $RootDir
try {
    python -m pytest --cov --cov-report=term-missing
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    cfn-lint infra/bootstrap.yaml infra/platform.yaml
    if ($LASTEXITCODE -ne 0) { throw "CloudFormation lint failed." }
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose validation failed." }
}
finally {
    Pop-Location
}

