from __future__ import annotations

import json

from shipment_platform.models import ShipmentStatus
from shipment_platform.worker import ShipmentRejectedError, Worker


def event_body(pending_shipment) -> str:
    return json.dumps(
        {
            "version": "0",
            "id": "eventbridge-envelope-id",
            "detail-type": "ShipmentRequested",
            "source": "shipment-event-platform.api",
            "detail": {
                "event_id": pending_shipment.request_event_id,
                "shipment_id": pending_shipment.shipment_id,
                "occurred_at": pending_shipment.created_at.isoformat(),
                "partner_reference": pending_shipment.partner_reference,
                "request": pending_shipment.request.model_dump(mode="json"),
            },
        }
    )


class NoopSqs:
    pass


class SuccessProcessor:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, shipment, request) -> None:
        self.calls += 1


class RetryProcessor:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, shipment, request) -> None:
        self.calls += 1
        raise RuntimeError("carrier temporarily unavailable")


class RejectProcessor:
    def dispatch(self, shipment, request) -> None:
        raise ShipmentRejectedError("destination is not serviceable")


def make_worker(repository, publisher, processor) -> Worker:
    return Worker(
        sqs_client=NoopSqs(),
        queue_url="queue-url",
        repository=repository,
        publisher=publisher,
        processor=processor,
        wait_time_seconds=1,
        visibility_timeout_seconds=120,
    )


def test_worker_success_is_idempotent(
    repository, recording_publisher, pending_shipment
):
    processor = SuccessProcessor()
    worker = make_worker(repository, recording_publisher, processor)
    message = {
        "MessageId": "sqs-message-1",
        "ReceiptHandle": "receipt-1",
        "Body": event_body(pending_shipment),
    }

    assert worker.handle_message(message) is True
    assert worker.handle_message(message) is True

    stored = repository.get(pending_shipment.shipment_id)
    assert stored.status == ShipmentStatus.DISPATCHED
    assert stored.result_event_published is True
    assert processor.calls == 1
    assert len(recording_publisher.dispatched) == 1


def test_worker_retry_keeps_message_and_releases_processing_lease(
    repository, recording_publisher, pending_shipment
):
    processor = RetryProcessor()
    worker = make_worker(repository, recording_publisher, processor)
    message = {
        "MessageId": "sqs-message-retry",
        "ReceiptHandle": "receipt-retry",
        "Body": event_body(pending_shipment),
    }

    assert worker.handle_message(message) is False
    assert worker.handle_message(message) is False

    assert repository.get(pending_shipment.shipment_id).status == ShipmentStatus.PENDING
    assert processor.calls == 2
    assert recording_publisher.dispatched == []
    assert recording_publisher.failed == []


def test_permanent_rejection_publishes_failed_event(
    repository, recording_publisher, pending_shipment
):
    worker = make_worker(repository, recording_publisher, RejectProcessor())
    message = {
        "MessageId": "sqs-message-rejected",
        "ReceiptHandle": "receipt-rejected",
        "Body": event_body(pending_shipment),
    }

    assert worker.handle_message(message) is True

    stored = repository.get(pending_shipment.shipment_id)
    assert stored.status == ShipmentStatus.FAILED
    assert stored.failure_reason == "destination is not serviceable"
    assert len(recording_publisher.failed) == 1


def test_invalid_event_is_retried_for_dlq(repository, recording_publisher):
    worker = make_worker(repository, recording_publisher, SuccessProcessor())

    assert (
        worker.handle_message(
            {
                "MessageId": "bad-message",
                "ReceiptHandle": "bad-receipt",
                "Body": '{"not":"an EventBridge event"}',
            }
        )
        is False
    )

