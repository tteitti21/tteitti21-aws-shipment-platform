from __future__ import annotations

import boto3

from shipment_platform.events import EventPublisher
from shipment_platform.models import ShipmentStatus
from shipment_platform.service import ShipmentService
from shipment_platform.worker import Worker

from tests.unit.test_worker import SuccessProcessor, event_body


def test_api_persists_and_publishes_to_mocked_eventbridge(
    aws_context, dynamodb_client, repository, valid_request
):
    events = boto3.client("events", region_name="eu-north-1")
    events.create_event_bus(Name="shipment-bus")
    service = ShipmentService(repository, EventPublisher(events, "shipment-bus"))

    accepted = service.create(
        request=valid_request,
        idempotency_key="mocked-aws-integration",
    )

    assert accepted.status == ShipmentStatus.PENDING
    assert repository.get(accepted.shipment_id).partner_reference == "partner-order-123"


def test_sqs_poll_processes_and_deletes_message(
    aws_context, repository, pending_shipment, recording_publisher
):
    sqs = boto3.client("sqs", region_name="eu-north-1")
    queue_url = sqs.create_queue(QueueName="processing")["QueueUrl"]
    sqs.send_message(QueueUrl=queue_url, MessageBody=event_body(pending_shipment))
    worker = Worker(
        sqs_client=sqs,
        queue_url=queue_url,
        repository=repository,
        publisher=recording_publisher,
        processor=SuccessProcessor(),
        wait_time_seconds=1,
        visibility_timeout_seconds=30,
    )

    assert worker.poll_once() == 1
    assert sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=1).get("Messages") is None
    assert repository.get(pending_shipment.shipment_id).status == ShipmentStatus.DISPATCHED

