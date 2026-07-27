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
    "CONSOLE_SCOPES"
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

    Write-Host "Starting the local shipment console at http://127.0.0.1:8088"
    Write-Host "The Cognito client secret and access token are never sent to the browser."

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
}
