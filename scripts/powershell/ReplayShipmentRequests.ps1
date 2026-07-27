[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [DateTimeOffset]$StartTime,

    [Parameter(Mandatory = $true)]
    [DateTimeOffset]$EndTime,

    [Parameter(Mandatory = $true)]
    [ValidateSet("REPLAY")]
    [string]$Confirm,

    [ValidatePattern("^[A-Za-z0-9._-]{1,64}$")]
    [string]$ReplayName = (
        "shipment-requests-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
    ),

    [string]$ProjectName = "shipment-event-platform",
    [string]$Environment = "dev",
    [string]$AwsRegion = "eu-north-1",
    [string]$PlatformStack = "$ProjectName-$Environment"
)

$ErrorActionPreference = "Stop"

if ($EndTime -le $StartTime) {
    throw "EndTime must be later than StartTime."
}

function Get-StackOutput {
    param([Parameter(Mandatory = $true)][string]$OutputKey)

    $Value = aws cloudformation describe-stacks `
        --region $AwsRegion `
        --stack-name $PlatformStack `
        --query "Stacks[0].Outputs[?OutputKey=='$OutputKey'].OutputValue | [0]" `
        --output text
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read $OutputKey from stack $PlatformStack."
    }
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -eq "None") {
        throw "Stack output $OutputKey is missing. Deploy the archive update first."
    }
    return $Value.Trim()
}

$ArchiveArn = Get-StackOutput -OutputKey "ShipmentRequestArchiveArn"
$EventBusArn = Get-StackOutput -OutputKey "EventBusArn"
$RequestedRuleArn = Get-StackOutput -OutputKey "ShipmentRequestedRuleArn"
$InvariantCulture = [Globalization.CultureInfo]::InvariantCulture
$AwsTimestampFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
$StartUtc = $StartTime.UtcDateTime.ToString(
    $AwsTimestampFormat,
    $InvariantCulture
)
$EndUtc = $EndTime.UtcDateTime.ToString(
    $AwsTimestampFormat,
    $InvariantCulture
)
$Destination = "Arn=$EventBusArn,FilterArns=$RequestedRuleArn"

Write-Warning (
    "Replaying archived ShipmentRequested events from $StartUtc through $EndUtc. " +
    "Matching events will be sent to SQS and may repair incomplete processing."
)

aws events start-replay `
    --region $AwsRegion `
    --replay-name $ReplayName `
    --description "Controlled shipment request replay for $PlatformStack" `
    --event-source-arn $ArchiveArn `
    --event-start-time $StartUtc `
    --event-end-time $EndUtc `
    --destination $Destination `
    --query "{ReplayArn:ReplayArn,State:State,StateReason:StateReason}" `
    --output table
if ($LASTEXITCODE -ne 0) {
    throw "Could not start EventBridge replay $ReplayName."
}

Write-Host "Replay started: $ReplayName"
Write-Host "Inspect it with:"
Write-Host (
    "aws events describe-replay --region $AwsRegion " +
    "--replay-name $ReplayName --output table"
)
