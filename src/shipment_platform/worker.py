from __future__ import annotations

import logging
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from .aws import boto_client
from .config import Settings, get_settings
from .events import EventPublisher
from .logging import configure_logging
from .models import (
    EventBridgeEnvelope,
    ShipmentCreate,
    ShipmentRecord,
    ShipmentStatus,
)
from .repository import ShipmentNotFoundError, ShipmentRepository


logger = logging.getLogger(__name__)


class RetryableProcessingError(RuntimeError):
    """An operational failure that should leave the SQS message for retry."""


class ShipmentRejectedError(RuntimeError):
    """A permanent business failure that completes the shipment as FAILED."""


class Processor(Protocol):
    def dispatch(self, shipment: ShipmentRecord, request: ShipmentCreate) -> None: ...


class ShipmentProcessor:
    """Learning processor; replace this boundary with a carrier integration."""

    def dispatch(self, shipment: ShipmentRecord, request: ShipmentCreate) -> None:
        logger.info(
            "shipment dispatched by learning processor",
            extra={
                "shipment_id": shipment.shipment_id,
                "event_id": shipment.request_event_id,
            },
        )


def result_event_id(request_event_id: str, status: ShipmentStatus) -> str:
    return str(uuid5(NAMESPACE_URL, f"{request_event_id}:{status}"))


@dataclass
class Worker:
    sqs_client: object
    queue_url: str
    repository: ShipmentRepository
    publisher: EventPublisher
    processor: Processor
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 120

    def handle_message(self, message: dict) -> bool:
        """Return True only when the message is safe to delete."""
        sqs_message_id = message.get("MessageId")
        try:
            envelope = EventBridgeEnvelope.model_validate_json(message["Body"])
        except (KeyError, ValidationError, ValueError):
            logger.exception(
                "invalid SQS event; retaining for redrive to DLQ",
                extra={"sqs_message_id": sqs_message_id},
            )
            return False

        detail = envelope.detail
        context = {
            "shipment_id": detail.shipment_id,
            "event_id": detail.event_id,
            "sqs_message_id": sqs_message_id,
        }
        try:
            shipment = self.repository.get(detail.shipment_id)
        except ShipmentNotFoundError:
            logger.exception("shipment record is not visible yet", extra=context)
            return False

        if shipment.request_event_id != detail.event_id:
            logger.error("request event does not match stored shipment", extra=context)
            return False

        if shipment.status in (ShipmentStatus.DISPATCHED, ShipmentStatus.FAILED):
            return self._ensure_result_published(shipment, context)

        processing_owner = sqs_message_id or message.get("ReceiptHandle", "unknown")
        now_epoch = int(datetime.now(UTC).timestamp())
        if not self.repository.claim_processing(
            shipment_id=detail.shipment_id,
            request_event_id=detail.event_id,
            processing_owner=processing_owner,
            lease_until_epoch=now_epoch + self.visibility_timeout_seconds,
            now_epoch=now_epoch,
        ):
            logger.info("duplicate delivery did not acquire processing lease", extra=context)
            return False

        try:
            self.processor.dispatch(shipment, detail.request)
            status = ShipmentStatus.DISPATCHED
            reason = None
        except ShipmentRejectedError as error:
            status = ShipmentStatus.FAILED
            reason = str(error)[:500]
        except Exception:
            self.repository.release_processing_claim(
                shipment_id=detail.shipment_id,
                processing_owner=processing_owner,
            )
            logger.exception("retryable shipment processing failure", extra=context)
            return False

        event_id = result_event_id(detail.event_id, status)
        shipment, _transitioned = self.repository.complete(
            shipment_id=detail.shipment_id,
            request_event_id=detail.event_id,
            processing_owner=processing_owner,
            result_event_id=event_id,
            status=status,
            failure_reason=reason,
            now=datetime.now(UTC),
        )
        return self._ensure_result_published(shipment, context)

    def _ensure_result_published(
        self, shipment: ShipmentRecord, context: dict
    ) -> bool:
        if shipment.result_event_published:
            logger.info("duplicate delivery already completed", extra=context)
            return True
        if not shipment.result_event_id:
            logger.error("terminal shipment is missing result event ID", extra=context)
            return False
        try:
            if shipment.status == ShipmentStatus.DISPATCHED:
                self.publisher.shipment_dispatched(
                    event_id=shipment.result_event_id,
                    shipment_id=shipment.shipment_id,
                    request_event_id=shipment.request_event_id,
                    occurred_at=shipment.updated_at,
                )
            elif shipment.status == ShipmentStatus.FAILED:
                self.publisher.shipment_failed(
                    event_id=shipment.result_event_id,
                    shipment_id=shipment.shipment_id,
                    request_event_id=shipment.request_event_id,
                    reason=shipment.failure_reason or "shipment processing failed",
                    occurred_at=shipment.updated_at,
                )
            else:
                return False
            self.repository.mark_result_published(
                shipment_id=shipment.shipment_id,
                result_event_id=shipment.result_event_id,
            )
            logger.info("shipment result published", extra=context)
            return True
        except Exception:
            logger.exception("result publication failed; message will retry", extra=context)
            return False

    def poll_once(self) -> int:
        response = self.sqs_client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=self.wait_time_seconds,
            VisibilityTimeout=self.visibility_timeout_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        handled = 0
        for message in response.get("Messages", []):
            if self.handle_message(message):
                self.sqs_client.delete_message(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                )
                handled += 1
        return handled

    def run(self, stop_event: threading.Event) -> None:
        logger.info("worker started")
        while not stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("SQS poll failed")
                stop_event.wait(1)
        logger.info("worker stopped")


def build_worker(settings: Settings) -> Worker:
    return Worker(
        sqs_client=boto_client("sqs", settings),
        queue_url=settings.processing_queue_url,
        repository=ShipmentRepository(
            boto_client("dynamodb", settings),
            settings.table_name,
            settings.shipment_retention_days,
        ),
        publisher=EventPublisher(
            boto_client("events", settings), settings.event_bus_name
        ),
        processor=ShipmentProcessor(),
        wait_time_seconds=settings.sqs_wait_time_seconds,
        visibility_timeout_seconds=settings.sqs_visibility_timeout_seconds,
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = threading.Event()

    def stop(_signum, _frame) -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    build_worker(settings).run(stop_event)


if __name__ == "__main__":
    main()
