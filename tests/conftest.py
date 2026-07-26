from __future__ import annotations

import os
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from shipment_platform.models import ShipmentCreate
from shipment_platform.repository import ShipmentRepository


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-north-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")


@pytest.fixture
def valid_payload() -> dict:
    return {
        "partner_reference": "partner-order-123",
        "origin": {
            "name": "North Warehouse",
            "address_line1": "1 Origin Road",
            "city": "Helsinki",
            "postal_code": "00100",
            "country_code": "FI",
        },
        "destination": {
            "name": "South Store",
            "address_line1": "9 Destination Street",
            "city": "Turku",
            "postal_code": "20100",
            "country_code": "FI",
        },
        "packages": [{"weight_kg": "2.50", "description": "Books"}],
    }


@pytest.fixture
def valid_request(valid_payload) -> ShipmentCreate:
    return ShipmentCreate.model_validate(valid_payload)


@pytest.fixture
def aws_context():
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_client(aws_context):
    client = boto3.client("dynamodb", region_name="eu-north-1")
    client.create_table(
        TableName="shipments",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
    )
    return client


@pytest.fixture
def repository(dynamodb_client) -> ShipmentRepository:
    return ShipmentRepository(dynamodb_client, "shipments", retention_days=30)


@pytest.fixture
def pending_shipment(repository, valid_request):
    return repository.create_or_get(
        shipment_id="shp_test123",
        idempotency_key="idem-test-123",
        request_event_id="11111111-1111-4111-8111-111111111111",
        request=valid_request,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    ).shipment


class RecordingPublisher:
    def __init__(self) -> None:
        self.requested: list[dict] = []
        self.dispatched: list[dict] = []
        self.failed: list[dict] = []

    def shipment_requested(self, **kwargs) -> None:
        self.requested.append(kwargs)

    def shipment_dispatched(self, **kwargs) -> None:
        self.dispatched.append(kwargs)

    def shipment_failed(self, **kwargs) -> None:
        self.failed.append(kwargs)


@pytest.fixture
def recording_publisher() -> RecordingPublisher:
    return RecordingPublisher()

