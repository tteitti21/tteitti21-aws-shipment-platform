[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9_.-]+$")]
    [string]$ImageTag,
    [string]$ProjectName = "shipment-event-platform",
    [string]$AwsRegion = "eu-north-1",
    [string]$BootstrapStack = "$ProjectName-bootstrap"
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path

$ApiRepositoryUri = aws cloudformation describe-stacks `
    --region $AwsRegion `
    --stack-name $BootstrapStack `
    --query "Stacks[0].Outputs[?OutputKey=='ApiRepositoryUri'].OutputValue | [0]" `
    --output text
if ($LASTEXITCODE -ne 0) { throw "Could not read API repository URI." }

$WorkerRepositoryUri = aws cloudformation describe-stacks `
    --region $AwsRegion `
    --stack-name $BootstrapStack `
    --query "Stacks[0].Outputs[?OutputKey=='WorkerRepositoryUri'].OutputValue | [0]" `
    --output text
if ($LASTEXITCODE -ne 0) { throw "Could not read worker repository URI." }

$Registry = $ApiRepositoryUri.Split("/")[0]
aws ecr get-login-password --region $AwsRegion |
    docker login --username AWS --password-stdin $Registry
if ($LASTEXITCODE -ne 0) { throw "ECR login failed." }

docker tag "${ProjectName}-api:${ImageTag}" "${ApiRepositoryUri}:${ImageTag}"
docker tag "${ProjectName}-worker:${ImageTag}" "${WorkerRepositoryUri}:${ImageTag}"
docker push "${ApiRepositoryUri}:${ImageTag}"
if ($LASTEXITCODE -ne 0) { throw "API image push failed." }
docker push "${WorkerRepositoryUri}:${ImageTag}"
if ($LASTEXITCODE -ne 0) { throw "Worker image push failed." }

$ApiDigest = aws ecr describe-images `
    --region $AwsRegion `
    --repository-name "$ProjectName-api" `
    --image-ids "imageTag=$ImageTag" `
    --query "imageDetails[0].imageDigest" `
    --output text
$WorkerDigest = aws ecr describe-images `
    --region $AwsRegion `
    --repository-name "$ProjectName-worker" `
    --image-ids "imageTag=$ImageTag" `
    --query "imageDetails[0].imageDigest" `
    --output text
if ($LASTEXITCODE -ne 0) { throw "Could not resolve image digests." }

$DeploymentFile = Join-Path $RootDir ".deployment.env"
@(
    "API_IMAGE_URI=${ApiRepositoryUri}@${ApiDigest}"
    "WORKER_IMAGE_URI=${WorkerRepositoryUri}@${WorkerDigest}"
) | Set-Content -Path $DeploymentFile -Encoding utf8

Write-Host "Wrote immutable image URIs to $DeploymentFile (gitignored)."

