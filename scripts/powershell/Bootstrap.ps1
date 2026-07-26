[CmdletBinding()]
param(
    [string]$ProjectName = "shipment-event-platform",
    [string]$AwsRegion = "eu-north-1",
    [string]$BootstrapStack = "$ProjectName-bootstrap"
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path

aws cloudformation deploy `
    --region $AwsRegion `
    --stack-name $BootstrapStack `
    --template-file (Join-Path $RootDir "infra/bootstrap.yaml") `
    --parameter-overrides "ProjectName=$ProjectName" `
    --no-fail-on-empty-changeset
if ($LASTEXITCODE -ne 0) { throw "Bootstrap deployment failed." }

aws cloudformation describe-stacks `
    --region $AwsRegion `
    --stack-name $BootstrapStack `
    --query "Stacks[0].Outputs" `
    --output table
if ($LASTEXITCODE -ne 0) { throw "Could not read bootstrap outputs." }

