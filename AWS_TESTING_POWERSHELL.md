# AWS-Side Testing Guide (PowerShell)

This guide tests the deployed `shipment-event-platform` from PowerShell. Run the
commands from the project root:

```powershell
cd C:\aws-shipment-event-platform
```

Most sections are read-only or create normal test shipments. Sections explicitly
marked **optional/state-changing** temporarily change an ECS desired count, send a
deliberately malformed SQS message, or create an SNS subscription.

Never print, save, or commit the Cognito client secret or access token.

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

Normally both services should be `ACTIVE`, with desired count `1`, running count
`1`, and pending count `0`.

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

## 16. Optional/state-changing: pause the worker to observe `PENDING`

Set only the worker desired count to zero:

```powershell
aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service worker `
    --desired-count 0

aws ecs wait services-stable `
    --region $Region `
    --cluster $ClusterName `
    --services worker
```

Submit a new shipment with a new idempotency key. It should remain `PENDING`, and
the processing queue should contain approximately one message.

Restore the worker:

```powershell
aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service worker `
    --desired-count 1

aws ecs wait services-stable `
    --region $Region `
    --cluster $ClusterName `
    --services worker
```

The queued shipment should then become `DISPATCHED`. Restore the desired count to
`1` after this test.

## 17. Optional/state-changing: pause both services to reduce Fargate cost

Stop the running Fargate tasks without deleting the stack:

```powershell
aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service api `
    --desired-count 0

aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service worker `
    --desired-count 0
```

Restore both services later:

```powershell
aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service api `
    --desired-count 1

aws ecs update-service `
    --region $Region `
    --cluster $ClusterName `
    --service worker `
    --desired-count 1

aws ecs wait services-stable `
    --region $Region `
    --cluster $ClusterName `
    --services api worker
```

While both services are at zero, Fargate compute charges stop, but the ALB, VPC
Link, storage, and other provisioned resources may still incur charges. Manual
scaling also causes temporary CloudFormation drift until the counts are restored.

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

## 20. Clear credentials from PowerShell memory

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

