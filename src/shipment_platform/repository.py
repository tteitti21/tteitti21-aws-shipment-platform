from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from .models import ShipmentCreate, ShipmentRecord, ShipmentStatus


class IdempotencyConflictError(Exception):
    """The same idempotency key was reused for a different request."""


class ShipmentNotFoundError(Exception):
    """No shipment exists for the requested identifier."""


@dataclass(frozen=True)
class CreateResult:
    shipment: ShipmentRecord
    created: bool


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_request(request: ShipmentCreate) -> str:
    return json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def request_hash(request: ShipmentCreate) -> str:
    return hashlib.sha256(canonical_request(request).encode("utf-8")).hexdigest()


def _to_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb(item) for item in value]
    return value


class ShipmentRepository:
    def __init__(
        self,
        dynamodb_client,
        table_name: str,
        retention_days: int = 30,
    ) -> None:
        self._client = dynamodb_client
        self._table_name = table_name
        self._retention_seconds = retention_days * 86400

    def create_or_get(
        self,
        *,
        shipment_id: str,
        idempotency_key: str,
        request_event_id: str,
        request: ShipmentCreate,
        now: datetime | None = None,
    ) -> CreateResult:
        timestamp = now or utc_now()
        timestamp_text = timestamp.isoformat()
        digest = request_hash(request)
        expires_at = int(timestamp.timestamp()) + self._retention_seconds
        shipment_item = {
            "pk": {"S": f"SHIPMENT#{shipment_id}"},
            "entity_type": {"S": "SHIPMENT"},
            "shipment_id": {"S": shipment_id},
            "idempotency_key": {"S": idempotency_key},
            "request_hash": {"S": digest},
            "request_event_id": {"S": request_event_id},
            "partner_reference": {"S": request.partner_reference},
            "status": {"S": ShipmentStatus.PENDING},
            "request": {
                "M": self._serialize_map(
                    _to_dynamodb(request.model_dump(mode="json"))
                )
            },
            "created_at": {"S": timestamp_text},
            "updated_at": {"S": timestamp_text},
            "result_event_published": {"BOOL": False},
            "expires_at": {"N": str(expires_at)},
        }
        idempotency_item = {
            "pk": {"S": f"IDEMPOTENCY#{idempotency_key}"},
            "entity_type": {"S": "IDEMPOTENCY"},
            "shipment_id": {"S": shipment_id},
            "request_hash": {"S": digest},
            "expires_at": {"N": str(expires_at)},
        }
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": idempotency_item,
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": shipment_item,
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ],
                ClientRequestToken=request_event_id,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            return CreateResult(
                shipment=self._get_by_idempotency_key(
                    idempotency_key=idempotency_key,
                    expected_hash=digest,
                ),
                created=False,
            )
        return CreateResult(shipment=self._deserialize(shipment_item), created=True)

    def _get_by_idempotency_key(
        self, *, idempotency_key: str, expected_hash: str
    ) -> ShipmentRecord:
        mapping = None
        for attempt in range(3):
            response = self._client.get_item(
                TableName=self._table_name,
                Key={"pk": {"S": f"IDEMPOTENCY#{idempotency_key}"}},
                ConsistentRead=True,
            )
            mapping = response.get("Item")
            if mapping:
                break
            time.sleep(0.05 * (attempt + 1))
        if not mapping:
            raise RuntimeError("idempotency transaction conflict could not be resolved")
        if mapping["request_hash"]["S"] != expected_hash:
            raise IdempotencyConflictError(idempotency_key)
        return self.get(mapping["shipment_id"]["S"])

    def get(self, shipment_id: str) -> ShipmentRecord:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"pk": {"S": f"SHIPMENT#{shipment_id}"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            raise ShipmentNotFoundError(shipment_id)
        return self._deserialize(item)

    def complete(
        self,
        *,
        shipment_id: str,
        request_event_id: str,
        processing_owner: str,
        result_event_id: str,
        status: ShipmentStatus,
        failure_reason: str | None = None,
        now: datetime | None = None,
    ) -> tuple[ShipmentRecord, bool]:
        timestamp = (now or utc_now()).isoformat()
        values: dict[str, Any] = {
            ":pending": {"S": ShipmentStatus.PENDING},
            ":status": {"S": status},
            ":updated_at": {"S": timestamp},
            ":request_event_id": {"S": request_event_id},
            ":processing_owner": {"S": processing_owner},
            ":result_event_id": {"S": result_event_id},
            ":false": {"BOOL": False},
        }
        update = (
            "SET #status = :status, updated_at = :updated_at, "
            "result_event_id = :result_event_id, result_event_published = :false"
        )
        if failure_reason is not None:
            update += ", failure_reason = :failure_reason"
            values[":failure_reason"] = {"S": failure_reason}
        try:
            response = self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": f"SHIPMENT#{shipment_id}"}},
                UpdateExpression=update,
                ConditionExpression=(
                    "#status = :pending AND request_event_id = :request_event_id "
                    "AND processing_owner = :processing_owner"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
            return self._deserialize(response["Attributes"]), True
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self.get(shipment_id), False

    def claim_processing(
        self,
        *,
        shipment_id: str,
        request_event_id: str,
        processing_owner: str,
        lease_until_epoch: int,
        now_epoch: int,
    ) -> bool:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": f"SHIPMENT#{shipment_id}"}},
                UpdateExpression=(
                    "SET processing_owner = :processing_owner, "
                    "processing_lease_until = :lease_until"
                ),
                ConditionExpression=(
                    "#status = :pending AND request_event_id = :request_event_id "
                    "AND (attribute_not_exists(processing_lease_until) "
                    "OR processing_lease_until < :now)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": {"S": ShipmentStatus.PENDING},
                    ":request_event_id": {"S": request_event_id},
                    ":processing_owner": {"S": processing_owner},
                    ":lease_until": {"N": str(lease_until_epoch)},
                    ":now": {"N": str(now_epoch)},
                },
            )
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_processing_claim(
        self, *, shipment_id: str, processing_owner: str
    ) -> None:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": f"SHIPMENT#{shipment_id}"}},
                UpdateExpression="REMOVE processing_owner, processing_lease_until",
                ConditionExpression="processing_owner = :processing_owner",
                ExpressionAttributeValues={
                    ":processing_owner": {"S": processing_owner}
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def mark_result_published(
        self, *, shipment_id: str, result_event_id: str
    ) -> None:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": f"SHIPMENT#{shipment_id}"}},
                UpdateExpression="SET result_event_published = :true",
                ConditionExpression="result_event_id = :result_event_id",
                ExpressionAttributeValues={
                    ":true": {"BOOL": True},
                    ":result_event_id": {"S": result_event_id},
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    @staticmethod
    def _serialize_map(value: dict[str, Any]) -> dict[str, Any]:
        from boto3.dynamodb.types import TypeSerializer

        serializer = TypeSerializer()
        return {key: serializer.serialize(item) for key, item in value.items()}

    @staticmethod
    def _deserialize(item: dict[str, Any]) -> ShipmentRecord:
        from boto3.dynamodb.types import TypeDeserializer

        deserializer = TypeDeserializer()
        plain = {key: deserializer.deserialize(value) for key, value in item.items()}
        return ShipmentRecord(
            shipment_id=plain["shipment_id"],
            partner_reference=plain["partner_reference"],
            status=plain["status"],
            created_at=plain["created_at"],
            updated_at=plain["updated_at"],
            failure_reason=plain.get("failure_reason"),
            idempotency_key=plain["idempotency_key"],
            request=plain["request"],
            request_event_id=plain["request_event_id"],
            result_event_id=plain.get("result_event_id"),
            result_event_published=plain.get("result_event_published", False),
        )
