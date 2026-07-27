[CmdletBinding()]
param(
    [string]$ProjectName = "shipment-event-platform",
    [string]$Environment = "dev",
    [string]$AwsRegion = "eu-north-1",
    [string]$PlatformStack = "$ProjectName-$Environment",
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$EnvironmentNames = @(
    "CONSOLE_API_URL",
    "CONSOLE_TOKEN_URL",
    "CONSOLE_CLIENT_ID",
    "CONSOLE_CLIENT_SECRET",
    "CONSOLE_SCOPES",
    "CONSOLE_SNS_TOPIC_ARN",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN"
)
$PreviousValues = @{}

function Get-StackOutput {
    param([Parameter(Mandatory = $true)][string]$OutputKey)

    $Value = aws cloudformation describe-stacks `
        --region $AwsRegion `
        --stack-name $PlatformStack `
        --query "Stacks[0].Outputs[?OutputKey=='$OutputKey'].OutputValue | [0]" `
        --output text
    if ($LASTEXITCODE -ne 0 -or -not $Value -or $Value -eq "None") {
        throw "Could not read CloudFormation output $OutputKey."
    }
    return $Value
}

foreach ($Name in $EnvironmentNames) {
    $PreviousValues[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    $env:CONSOLE_API_URL = Get-StackOutput "ApiUrl"
    $env:CONSOLE_TOKEN_URL = Get-StackOutput "CognitoTokenUrl"
    $env:CONSOLE_CLIENT_ID = Get-StackOutput "CognitoClientId"
    $env:CONSOLE_SNS_TOPIC_ARN = Get-StackOutput "ShipmentResultTopicArn"
    $UserPoolId = Get-StackOutput "CognitoUserPoolId"

    $env:CONSOLE_CLIENT_SECRET = aws cognito-idp describe-user-pool-client `
        --region $AwsRegion `
        --user-pool-id $UserPoolId `
        --client-id $env:CONSOLE_CLIENT_ID `
        --query "UserPoolClient.ClientSecret" `
        --output text
    if (
        $LASTEXITCODE -ne 0 `
        -or -not $env:CONSOLE_CLIENT_SECRET `
        -or $env:CONSOLE_CLIENT_SECRET -eq "None"
    ) {
        throw "Could not retrieve the Cognito app client secret."
    }

    $env:CONSOLE_SCOPES = (
        "shipment-api/shipments.write shipment-api/shipments.read"
    )
    $env:AWS_REGION = $AwsRegion

    $CredentialJson = aws configure export-credentials --format process
    if ($LASTEXITCODE -ne 0 -or -not $CredentialJson) {
        throw "Could not export temporary credentials from the active AWS CLI session."
    }
    try {
        $Credentials = $CredentialJson | ConvertFrom-Json
    }
    catch {
        throw "AWS CLI returned an invalid credential response."
    }
    if (-not $Credentials.AccessKeyId -or -not $Credentials.SecretAccessKey) {
        throw "AWS CLI did not return usable temporary credentials."
    }
    $env:AWS_ACCESS_KEY_ID = $Credentials.AccessKeyId
    $env:AWS_SECRET_ACCESS_KEY = $Credentials.SecretAccessKey
    $env:AWS_SESSION_TOKEN = $Credentials.SessionToken

    Write-Host "Starting the local shipment console at http://127.0.0.1:8088"
    Write-Host "The Cognito client secret and access token are never sent to the browser."
    Write-Host "SNS controls are restricted to email on the stack's result topic."

    $ComposeArguments = @(
        "compose",
        "--profile",
        "console",
        "up",
        "--build"
    )
    if ($Detached) {
        $ComposeArguments += "--detach"
    }
    $ComposeArguments += "console"

    Push-Location $RootDir
    try {
        & docker $ComposeArguments
        if ($LASTEXITCODE -ne 0) {
            throw "The local console container failed to start."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($Name in $EnvironmentNames) {
        [Environment]::SetEnvironmentVariable(
            $Name,
            $PreviousValues[$Name],
            "Process"
        )
    }
    $UserPoolId = $null
    $CredentialJson = $null
    $Credentials = $null
}
