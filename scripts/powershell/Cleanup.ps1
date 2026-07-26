[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("DELETE")]
    [string]$Confirm,
    [string]$ProjectName = "shipment-event-platform",
    [string]$Environment = "dev",
    [string]$AwsRegion = "eu-north-1",
    [string]$PlatformStack = "$ProjectName-$Environment",
    [string]$BootstrapStack = "$ProjectName-bootstrap"
)

$ErrorActionPreference = "Stop"
Write-Warning "Deleting DynamoDB data, queues, logs, and ECR images."

aws cloudformation delete-stack --region $AwsRegion --stack-name $PlatformStack
if ($LASTEXITCODE -ne 0) { throw "Could not start platform deletion." }
aws cloudformation wait stack-delete-complete --region $AwsRegion --stack-name $PlatformStack
if ($LASTEXITCODE -ne 0) { throw "Platform deletion did not complete." }

aws cloudformation delete-stack --region $AwsRegion --stack-name $BootstrapStack
if ($LASTEXITCODE -ne 0) { throw "Could not start bootstrap deletion." }
aws cloudformation wait stack-delete-complete --region $AwsRegion --stack-name $BootstrapStack
if ($LASTEXITCODE -ne 0) { throw "Bootstrap deletion did not complete." }

Write-Host "Deleted $PlatformStack and $BootstrapStack."

