# AWS-Side Testing Guide (PowerShell)

## Guide index

Use the links to jump directly to a section. The line numbers make the same
sections easy to locate in an editor; links remain more reliable after edits.

| Section | Instructions | Starts at line |
| ---: | --- | ---: |
| 1 | [Load CloudFormation outputs](#1-load-cloudformation-outputs) | 58 |
| 2 | [Check the CloudFormation stack and ECS services](#2-check-the-cloudformation-stack-and-ecs-services) | 123 |
| 3 | [Check the internal ALB target](#3-check-the-internal-alb-target) | 190 |
| 4 | [Verify API Gateway rejects an unauthenticated request](#4-verify-that-api-gateway-rejects-an-unauthenticated-request) | 221 |
| 5 | [Obtain a machine-to-machine access token](#5-obtain-a-machine-to-machine-access-token) | 232 |
| 6 | [Optionally follow logs while testing](#6-optionally-follow-logs-while-testing) | 282 |
| 7 | [Submit a shipment](#7-submit-a-shipment) | 314 |
| 8 | [Poll the shipment status](#8-poll-the-shipment-status) | 357 |
| 9 | [Inspect the shipment in DynamoDB](#9-inspect-the-shipment-in-dynamodb) | 387 |
| 10 | [Search recent logs for the shipment](#10-search-recent-logs-for-the-shipment) | 444 |
| 11 | [Check the processing queue and DLQ](#11-check-the-processing-queue-and-dlq) | 475 |
| 12 | [Test an idempotent replay](#12-test-an-idempotent-replay) | 504 |
| 13 | [Test an idempotency conflict](#13-test-an-idempotency-conflict) | 533 |
| 14 | [Test request validation](#14-test-request-validation) | 565 |
| 15 | [Test OAuth scope enforcement](#15-test-oauth-scope-enforcement) | 598 |
| 16 | [Test worker auto scaling from zero](#16-test-worker-auto-scaling-from-zero) | 651 |
| 17 | [Pause the API to reduce Fargate cost](#17-optionalstate-changing-pause-the-api-to-reduce-fargate-cost) | 734 |
| 18 | [Test SQS redrive to the DLQ](#18-optionalstate-changing-test-sqs-redrive-to-the-dlq) | 767 |
| 19 | [Create an SNS email subscription](#19-optionalstate-changing-create-an-sns-email-subscription) | 805 |
| 20 | [Replay archived shipment requests](#20-optionalstate-changing-replay-archived-shipment-requests) | 842 |
| 21 | [Clear credentials from PowerShell memory](#21-clear-credentials-from-powershell-memory) | 902 |
| Result | [Expected HTTP results](#expected-http-results) | 920 |

This guide tests the deployed `shipment-event-platform` from PowerShell. Run the
commands from the project root:

```powershell
cd C:\aws-shipment-event-platform
```

Most sections are read-only or create normal test shipments. Sections explicitly
marked **optional/state-changing** temporarily change the API ECS desired count,
send a deliberately malformed SQS message, create an SNS subscription, or replay
archived shipment requests.

Never print, save, or commit the Cognito client secret or access token.

If you prefer a browser GUI for token acquisition, POST, and GET, use the
optional local console documented under **Optional local browser test console**
in `README.md`. The commands below remain useful for AWS infrastructure
diagnostics that the GUI intentionally does not perform.

With Docker Desktop running, the one-command console launcher is:

```powershell
.\scripts\powershell\StartConsole.ps1
```

## 1. Load CloudFormation outputs

Set the deployed stack and region:

```powershell
$Stack = "shipment-event-platform-dev"
$Region = "eu-north-1"
```

Create a helper for reading one CloudFormation output:

```powershell
function Get-StackOutput([string]$Key) {
    aws cloudformation describe-stacks `
        --region $Region `
        --stack-name $Stack `
        --query "Stacks[0].Outputs[?OutputKey=='$Key'].OutputValue | [0]" `
        --output text
}
```

Load the values used by the remaining commands:

```powershell
$ApiUrl       = Get-StackOutput "ApiUrl"
$TokenUrl     = Get-StackOutput "CognitoTokenUrl"
$ClientId     = Get-StackOutput "CognitoClientId"
$UserPoolId   = Get-StackOutput "CognitoUserPoolId"
$QueueUrl     = Get-StackOutput "ProcessingQueueUrl"
$DlqUrl       = Get-StackOutput "DeadLetterQueueUrl"
$TableName    = Get-StackOutput "ShipmentTableName"
$ClusterName  = Get-StackOutput "EcsClusterName"
$TopicArn     = Get-StackOutput "ShipmentResultTopicArn"
$EventBusName = Get-StackOutput "EventBusName"
$ArchiveName  = Get-StackOutput "ShipmentRequestArchiveName"
$ArchiveArn   = Get-StackOutput "ShipmentRequestArchiveArn"
$RequestRule  = Get-StackOutput "ShipmentRequestedRuleArn"
$WorkerService = Get-StackOutput "WorkerServiceName"
$ScaleOutAlarm = Get-StackOutput "WorkerScaleOutAlarmName"
$ScaleInAlarm  = Get-StackOutput "WorkerScaleInAlarmName"
```

Display the non-secret resource identifiers:

```powershell
[pscustomobject]@{
    ApiUrl       = $ApiUrl
    TokenUrl     = $TokenUrl
    ClientId     = $ClientId
    UserPoolId   = $UserPoolId
    QueueUrl     = $QueueUrl
    DlqUrl       = $DlqUrl
    TableName    = $TableName
    ClusterName  = $ClusterName
    TopicArn     = $TopicArn
    EventBusName = $EventBusName
    ArchiveName  = $ArchiveName
    ArchiveArn   = $ArchiveArn
    RequestRule  = $RequestRule
    WorkerService = $WorkerService
    ScaleOutAlarm = $ScaleOutAlarm
    ScaleInAlarm  = $ScaleInAlarm
} | Format-List
```

## 2. Check the CloudFormation stack and ECS services

Check the stack:

```powershell
aws cloudformation describe-stacks `
    --region $Region `
    --stack-name $Stack `
    --query "Stacks[0].{Name:StackName,Status:StackStatus}" `
    --output table
```

Expected stack status:

```text
CREATE_COMPLETE
```

Check the API and worker services:

```powershell
aws ecs describe-services `
    --region $Region `
    --cluster $ClusterName `
    --services api worker `
    --query "services[].{
        Service:serviceName,
        Status:status,
        Desired:desiredCount,
        Running:runningCount,
        Pending:pendingCount
    }" `
    --output table
```

The API normally has desired/running count `1`. The worker remains `ACTIVE`, but
after an idle period its desired/running count should be `0`. It starts
automatically when SQS contains visible work.

Inspect the worker's scalable range:

```powershell
aws application-autoscaling describe-scalable-targets `
    --region $Region `
    --service-namespace ecs `
    --resource-ids "service/$ClusterName/$WorkerService" `
    --query "ScalableTargets[].{
        Minimum:MinCapacity,
        Maximum:MaxCapacity,
        Suspended:SuspendedState
    }" `
    --output table
```

The expected minimum is `0`, the default maximum is `4`, and dynamic scaling
should not be suspended.

List the running tasks:

```powershell
aws ecs list-tasks `
    --region $Region `
    --cluster $ClusterName `
    --query "taskArns" `
    --output table
```

## 3. Check the internal ALB target

Read the target group ARN from the CloudFormation stack:

```powershell
$TargetGroupArn = aws cloudformation describe-stack-resource `
    --region $Region `
    --stack-name $Stack `
    --logical-resource-id ApiTargetGroup `
    --query "StackResourceDetail.PhysicalResourceId" `
    --output text
```

Check the target health:

```powershell
aws elbv2 describe-target-health `
    --region $Region `
    --target-group-arn $TargetGroupArn `
    --query "TargetHealthDescriptions[].{
        Target:Target.Id,
        Port:Target.Port,
        State:TargetHealth.State,
        Reason:TargetHealth.Reason
    }" `
    --output table
```

The API target should be `healthy` on port `8000`. The ALB checks FastAPI's
internal `GET /health` endpoint.

## 4. Verify that API Gateway rejects an unauthenticated request

Call a protected route without a bearer token:

```powershell
curl.exe -i "$ApiUrl/shipments/not-a-real-shipment"
```

The expected response is HTTP `401`. This verifies that API Gateway rejects the
request before it reaches FastAPI.

## 5. Obtain a machine-to-machine access token

Retrieve the app client secret into a variable. Do not print this variable:

```powershell
$ClientSecret = aws cognito-idp describe-user-pool-client `
    --region $Region `
    --user-pool-id $UserPoolId `
    --client-id $ClientId `
    --query "UserPoolClient.ClientSecret" `
    --output text
```

Create the HTTP Basic authentication value:

```powershell
$Basic = [Convert]::ToBase64String(
    [Text.Encoding]::ASCII.GetBytes("${ClientId}:${ClientSecret}")
)
```

Request an access token containing both API scopes:

```powershell
$TokenResponse = Invoke-RestMethod `
    -Method Post `
    -Uri $TokenUrl `
    -Headers @{
        Authorization = "Basic $Basic"
    } `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{
        grant_type = "client_credentials"
        scope = "shipment-api/shipments.write shipment-api/shipments.read"
    }

$AccessToken = $TokenResponse.access_token
```

Display only non-sensitive token metadata:

```powershell
$TokenResponse |
    Select-Object token_type, expires_in |
    Format-List
```

Do not display `$AccessToken`. The default access-token lifetime is approximately
3,600 seconds.

## 6. Optionally follow logs while testing

Open separate PowerShell windows for these commands. Stop either command with
`Ctrl+C`.

API container logs:

```powershell
aws logs tail /ecs/shipment-event-platform/dev/api `
    --region $Region `
    --follow `
    --format short
```

Worker container logs:

```powershell
aws logs tail /ecs/shipment-event-platform/dev/worker `
    --region $Region `
    --follow `
    --format short
```

API Gateway access logs:

```powershell
aws logs tail /aws/apigateway/shipment-event-platform-dev `
    --region $Region `
    --follow `
    --format short
```

## 7. Submit a shipment

Load the example request:

```powershell
$RequestBody = Get-Content `
    -LiteralPath .\examples\create-shipment.json `
    -Raw
```

Generate a new idempotency key for this logical shipment:

```powershell
$IdempotencyKey = "manual-test-" + [guid]::NewGuid().ToString("N")
```

Submit the request:

```powershell
$CreateHttpResponse = Invoke-WebRequest `
    -UseBasicParsing `
    -Method Post `
    -Uri "$ApiUrl/shipments" `
    -Headers @{
        Authorization     = "Bearer $AccessToken"
        "Idempotency-Key" = $IdempotencyKey
    } `
    -ContentType "application/json" `
    -Body $RequestBody
```

Inspect the HTTP status and response:

```powershell
$CreateHttpResponse.StatusCode
$CreateResponse = $CreateHttpResponse.Content | ConvertFrom-Json
$CreateResponse | Format-List
$ShipmentId = $CreateResponse.shipment_id
```

The expected HTTP status is `202`. The initial response normally contains status
`PENDING`, although the worker may process the shipment almost immediately.

## 8. Poll the shipment status

Check the status for up to ten seconds:

```powershell
$StatusResponse = $null

for ($Attempt = 1; $Attempt -le 10; $Attempt++) {
    $StatusResponse = Invoke-RestMethod `
        -Method Get `
        -Uri "$ApiUrl/shipments/$ShipmentId" `
        -Headers @{
            Authorization = "Bearer $AccessToken"
        }

    Write-Host "Attempt $Attempt - status $($StatusResponse.status)"

    if ($StatusResponse.status -in @("DISPATCHED", "FAILED")) {
        break
    }

    Start-Sleep -Seconds 1
}

$StatusResponse | Format-List
```

The current learning processor normally changes a valid shipment to
`DISPATCHED`.

## 9. Inspect the shipment in DynamoDB

Build the shipment's DynamoDB key:

```powershell
$ShipmentKey = @{
    pk = @{
        S = "SHIPMENT#$ShipmentId"
    }
} | ConvertTo-Json -Compress
```

Read selected fields:

```powershell
aws dynamodb get-item `
    --region $Region `
    --table-name $TableName `
    --key $ShipmentKey `
    --consistent-read `
    --query "Item.{
        PrimaryKey:pk.S,
        Status:status.S,
        RequestEventId:request_event_id.S,
        ResultEventId:result_event_id.S,
        ResultPublished:result_event_published.BOOL
    }" `
    --output table
```

After successful processing, expect status `DISPATCHED`, a result event ID, and
`ResultPublished=True`.

Inspect the related idempotency mapping:

```powershell
$IdempotencyDynamoKey = @{
    pk = @{
        S = "IDEMPOTENCY#$IdempotencyKey"
    }
} | ConvertTo-Json -Compress

aws dynamodb get-item `
    --region $Region `
    --table-name $TableName `
    --key $IdempotencyDynamoKey `
    --consistent-read `
    --query "Item.{
        PrimaryKey:pk.S,
        ShipmentId:shipment_id.S,
        RequestHash:request_hash.S
    }" `
    --output table
```

The mapped shipment ID should equal `$ShipmentId`.

## 10. Search recent logs for the shipment

API logs:

```powershell
aws logs tail /ecs/shipment-event-platform/dev/api `
    --region $Region `
    --since 15m `
    --format short |
    Select-String $ShipmentId
```

Worker logs:

```powershell
aws logs tail /ecs/shipment-event-platform/dev/worker `
    --region $Region `
    --since 15m `
    --format short |
    Select-String $ShipmentId
```

API Gateway logs:

```powershell
aws logs tail /aws/apigateway/shipment-event-platform-dev `
    --region $Region `
    --since 15m `
    --format short
```

## 11. Check the processing queue and DLQ

Inspect the processing queue:

```powershell
aws sqs get-queue-attributes `
    --region $Region `
    --queue-url $QueueUrl `
    --attribute-names `
        ApproximateNumberOfMessages `
        ApproximateNumberOfMessagesNotVisible `
    --query Attributes `
    --output table
```

Inspect the dead-letter queue:

```powershell
aws sqs get-queue-attributes `
    --region $Region `
    --queue-url $DlqUrl `
    --attribute-names ApproximateNumberOfMessages `
    --query Attributes `
    --output table
```

Both queues should normally be empty after a successful shipment. SQS counts are
approximate and can take a short time to update.

## 12. Test an idempotent replay

Repeat the same request body with the same idempotency key:

```powershell
$ReplayResponse = Invoke-RestMethod `
    -Method Post `
    -Uri "$ApiUrl/shipments" `
    -Headers @{
        Authorization     = "Bearer $AccessToken"
        "Idempotency-Key" = $IdempotencyKey
    } `
    -ContentType "application/json" `
    -Body $RequestBody
```

Compare the shipment IDs:

```powershell
[pscustomobject]@{
    OriginalShipment = $ShipmentId
    ReplayShipment   = $ReplayResponse.shipment_id
    SameShipment     = $ShipmentId -eq $ReplayResponse.shipment_id
}
```

`SameShipment` should be `True`. The replay intentionally republishes the request
event, but the worker must not dispatch the completed shipment a second time.

## 13. Test an idempotency conflict

Change the request while reusing the same idempotency key:

```powershell
$ChangedRequest = $RequestBody | ConvertFrom-Json
$ChangedRequest.partner_reference = "different-partner-order"
$ChangedBody = $ChangedRequest | ConvertTo-Json -Depth 10
```

Submit it:

```powershell
try {
    Invoke-RestMethod `
        -Method Post `
        -Uri "$ApiUrl/shipments" `
        -Headers @{
            Authorization     = "Bearer $AccessToken"
            "Idempotency-Key" = $IdempotencyKey
        } `
        -ContentType "application/json" `
        -Body $ChangedBody
}
catch {
    Write-Host "HTTP status:" $_.Exception.Response.StatusCode.value__
    Write-Host "Response:" $_.ErrorDetails.Message
}
```

The expected response is HTTP `409`.

## 14. Test request validation

Create a request missing required fields:

```powershell
$InvalidBody = @{
    partner_reference = "incomplete-request"
} | ConvertTo-Json
```

Submit it using a new idempotency key:

```powershell
try {
    Invoke-RestMethod `
        -Method Post `
        -Uri "$ApiUrl/shipments" `
        -Headers @{
            Authorization     = "Bearer $AccessToken"
            "Idempotency-Key" = "invalid-test-$([guid]::NewGuid().ToString('N'))"
        } `
        -ContentType "application/json" `
        -Body $InvalidBody
}
catch {
    Write-Host "HTTP status:" $_.Exception.Response.StatusCode.value__
    Write-Host "Response:" $_.ErrorDetails.Message
}
```

The expected response is HTTP `422`. FastAPI rejects the request before writing
to DynamoDB or publishing to EventBridge.

## 15. Test OAuth scope enforcement

Request a token with only the read scope:

```powershell
$ReadTokenResponse = Invoke-RestMethod `
    -Method Post `
    -Uri $TokenUrl `
    -Headers @{
        Authorization = "Basic $Basic"
    } `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{
        grant_type = "client_credentials"
        scope = "shipment-api/shipments.read"
    }

$ReadOnlyToken = $ReadTokenResponse.access_token
```

GET should succeed:

```powershell
Invoke-RestMethod `
    -Method Get `
    -Uri "$ApiUrl/shipments/$ShipmentId" `
    -Headers @{
        Authorization = "Bearer $ReadOnlyToken"
    }
```

POST should fail because the token lacks `shipments.write`:

```powershell
try {
    Invoke-RestMethod `
        -Method Post `
        -Uri "$ApiUrl/shipments" `
        -Headers @{
            Authorization     = "Bearer $ReadOnlyToken"
            "Idempotency-Key" = "scope-test-$([guid]::NewGuid().ToString('N'))"
        } `
        -ContentType "application/json" `
        -Body $RequestBody
}
catch {
    Write-Host "HTTP status:" $_.Exception.Response.StatusCode.value__
    Write-Host "Response:" $_.ErrorDetails.Message
}
```

The expected response is HTTP `403`.

## 16. Test worker auto scaling from zero

First wait until the queue has no visible or in-flight messages. The scale-in
alarm requires five consecutive idle minutes, after which the worker should show
desired/running count `0`:

```powershell
aws cloudwatch describe-alarms `
    --region $Region `
    --alarm-names $ScaleOutAlarm $ScaleInAlarm `
    --query "MetricAlarms[].{
        Alarm:AlarmName,
        State:StateValue,
        Reason:StateReason
    }" `
    --output table

aws ecs describe-services `
    --region $Region `
    --cluster $ClusterName `
    --services $WorkerService `
    --query "services[0].{
        Desired:desiredCount,
        Running:runningCount,
        Pending:pendingCount
    }" `
    --output table
```

Now submit a normal shipment using section 6 with a fresh
`Idempotency-Key`. The POST still returns `202`; status may remain `PENDING`
during the cold start. Watch the alarm and service for up to five minutes:

```powershell
1..20 | ForEach-Object {
    $AlarmState = aws cloudwatch describe-alarms `
        --region $Region `
        --alarm-names $ScaleOutAlarm `
        --query "MetricAlarms[0].StateValue" `
        --output text

    $WorkerState = aws ecs describe-services `
        --region $Region `
        --cluster $ClusterName `
        --services $WorkerService `
        --query "services[0].{
            Desired:desiredCount,
            Running:runningCount,
            Pending:pendingCount
        }" `
        --output json

    Write-Host "$(Get-Date -Format T) alarm=$AlarmState worker=$WorkerState"
    Start-Sleep -Seconds 15
}
```

Expected order:

1. SQS reports at least one visible message.
2. The scale-out alarm becomes `ALARM`.
3. Application Auto Scaling changes desired count from zero to one (or more for
   a backlog).
4. ECS moves the task through `PENDING` to `RUNNING`.
5. The shipment becomes `DISPATCHED` and the queue drains.
6. Once visible and in-flight messages have both remained zero for five minutes,
   the scale-in policy returns desired count to zero.

The first item after an idle period commonly takes roughly one to three minutes.
This is expected scale-from-zero cold-start latency, not an API failure.

Inspect scaling decisions if the expected transition does not happen:

```powershell
aws application-autoscaling describe-scaling-activities `
    --region $Region `
    --service-namespace ecs `
    --resource-id "service/$ClusterName/$WorkerService" `
    --scalable-dimension ecs:service:DesiredCount `
    --max-results 10 `
    --output table
```

## 17. Optional/state-changing: pause the API to reduce Fargate cost

The worker already scales itself to zero after five idle minutes. Stop the
always-on API task without deleting the stack:

```powershell
aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service api `
    --desired-count 0
```

Restore the API later:

```powershell
aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service api `
    --desired-count 1

aws ecs wait services-stable `
    --region $Region `
    --cluster $ClusterName `
    --services api
```

While the API and idle worker are both at zero, Fargate compute charges stop.
Queued messages can still wake the worker. The ALB, VPC Link, CloudWatch alarms,
storage, and other provisioned resources may still incur charges. Manual API
scaling also causes temporary CloudFormation drift until it is restored.

## 18. Optional/state-changing: test SQS redrive to the DLQ

Send a deliberately malformed message to the processing queue:

```powershell
aws sqs send-message `
    --region $Region `
    --queue-url $QueueUrl `
    --message-body '{"deliberately":"invalid-event"}'
```

The worker rejects the invalid EventBridge envelope and deliberately does not
delete it. With a 120-second visibility timeout and a maximum receive count of
three, the test takes several minutes.

Follow the worker logs:

```powershell
aws logs tail /ecs/shipment-event-platform/dev/worker `
    --region $Region `
    --follow `
    --format short
```

Periodically check the DLQ:

```powershell
aws sqs get-queue-attributes `
    --region $Region `
    --queue-url $DlqUrl `
    --attribute-names ApproximateNumberOfMessages `
    --query Attributes `
    --output table
```

Eventually the DLQ count should become one. This tests retry/redrive behavior; it
does not test the business `ShipmentFailed` path.

## 19. Optional/state-changing: create an SNS email subscription

The platform creates an SNS topic but no subscriptions. To receive result events
by email, supply your address:

```powershell
$EmailAddress = "your-email@example.com"

aws sns subscribe `
    --region $Region `
    --topic-arn $TopicArn `
    --protocol email `
    --notification-endpoint $EmailAddress
```

Confirm the subscription using the email AWS sends, then submit another
shipment. SNS does not replay messages published before confirmation.

List subscriptions:

```powershell
aws sns list-subscriptions-by-topic `
    --region $Region `
    --topic-arn $TopicArn `
    --output table
```

To remove a confirmed manual subscription, copy its `SubscriptionArn` and run:

```powershell
$SubscriptionArn = "replace-with-confirmed-subscription-arn"

aws sns unsubscribe `
    --region $Region `
    --subscription-arn $SubscriptionArn
```

## 20. Optional/state-changing: replay archived shipment requests

The archive begins collecting `ShipmentRequested` events only after the
CloudFormation archive resource is deployed. Submit a normal test shipment, then
wait at least ten minutes before replaying its time window.

Inspect the archive:

```powershell
aws events describe-archive `
    --region $Region `
    --archive-name $ArchiveName `
    --query "{State:State,RetentionDays:RetentionDays,EventCount:EventCount,SizeBytes:SizeBytes}" `
    --output table
```

`EventCount` and `SizeBytes` can take up to 24 hours to reconcile, so zero in
those summary fields does not always mean the newest event is absent.

Choose a narrow UTC window around the shipment submission and start the replay:

```powershell
$ReplayEnd = (Get-Date).ToUniversalTime()
$ReplayStart = $ReplayEnd.AddMinutes(-30)
$ReplayName = "shipment-requests-$((Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'))"

.\scripts\powershell\ReplayShipmentRequests.ps1 `
    -StartTime $ReplayStart `
    -EndTime $ReplayEnd `
    -ReplayName $ReplayName `
    -Confirm REPLAY
```

The script obtains the archive, event-bus, and request-rule ARNs from the stack.
It passes only the request rule in `FilterArns`, so the replay enters the SQS
processing path and is not sent directly to the SNS result rules.

Inspect asynchronous replay progress:

```powershell
aws events describe-replay `
    --region $Region `
    --replay-name $ReplayName `
    --query "{State:State,Reason:StateReason,LastEventTime:EventLastReplayedTime}" `
    --output table
```

Expected final state:

```text
COMPLETED
```

For an already completed shipment, the worker should log that the duplicate is
already complete and delete the SQS message without running dispatch again. If
the original shipment was incomplete, replay can resume its processing.

The caller needs `cloudformation:DescribeStacks`, `events:DescribeArchive`,
`events:StartReplay`, and `events:DescribeReplay`.

## 21. Clear credentials from PowerShell memory

Remove sensitive variables:

```powershell
Remove-Variable `
    ClientSecret, `
    Basic, `
    TokenResponse, `
    AccessToken, `
    ReadTokenResponse, `
    ReadOnlyToken `
    -ErrorAction SilentlyContinue
```

Close any `aws logs tail --follow` commands with `Ctrl+C`. Closing PowerShell,
Docker Desktop, or the editor does not stop the deployed AWS resources.

## Expected HTTP results

| Test | Expected status |
|---|---:|
| Missing or invalid token | `401` |
| Valid token without the required scope | `403` |
| Valid token with an invalid shipment body | `422` |
| Same idempotency key with a different body | `409` |
| Valid accepted shipment | `202` |
| Unknown shipment with a valid read token | `404` |
