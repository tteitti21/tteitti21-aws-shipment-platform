[CmdletBinding()]
param(
    [string]$ProjectName = "shipment-event-platform",
    [string]$Environment = "dev",
    [string]$AwsRegion = "eu-north-1",
    [string]$PlatformStack = "$ProjectName-$Environment"
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$DeploymentFile = Join-Path $RootDir ".deployment.env"
if (-not (Test-Path -LiteralPath $DeploymentFile)) {
    throw "Missing $DeploymentFile; run Push.ps1 first."
}

$DeploymentValues = @{}
Get-Content -LiteralPath $DeploymentFile | ForEach-Object {
    if ($_ -match "^\s*(#|$)") {
        return
    }
    $Name, $Value = $_ -split "=", 2
    $DeploymentValues[$Name] = $Value
}
$ApiImageUri = $DeploymentValues["API_IMAGE_URI"]
$WorkerImageUri = $DeploymentValues["WORKER_IMAGE_URI"]
$DigestPattern = "@sha256:[a-f0-9]{64}$"
if ($ApiImageUri -notmatch $DigestPattern -or $WorkerImageUri -notmatch $DigestPattern) {
    throw "Both image URIs must use immutable sha256 digests."
}

aws cloudformation deploy `
    --region $AwsRegion `
    --stack-name $PlatformStack `
    --template-file (Join-Path $RootDir "infra/platform.yaml") `
    --capabilities CAPABILITY_IAM `
    --parameter-overrides `
        "ProjectName=$ProjectName" `
        "Environment=$Environment" `
        "ApiImageUri=$ApiImageUri" `
        "WorkerImageUri=$WorkerImageUri" `
    --no-fail-on-empty-changeset
if ($LASTEXITCODE -ne 0) { throw "Platform deployment failed." }

aws cloudformation describe-stacks `
    --region $AwsRegion `
    --stack-name $PlatformStack `
    --query "Stacks[0].Outputs" `
    --output table
if ($LASTEXITCODE -ne 0) { throw "Could not read platform outputs." }
