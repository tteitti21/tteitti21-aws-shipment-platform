from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .events import EventPublisher
from .models import ShipmentAccepted, ShipmentCreate, ShipmentView
from .repository import ShipmentRepository


logger = logging.getLogger(__name__)


@dataclass
class ShipmentService:
    repository: ShipmentRepository
    publisher: EventPublisher

    def create(
        self, *, request: ShipmentCreate, idempotency_key: str
    ) -> ShipmentAccepted:
        shipment_id = f"shp_{uuid4().hex}"
        event_id = str(uuid4())
        now = datetime.now(UTC)
        result = self.repository.create_or_get(
            shipment_id=shipment_id,
            idempotency_key=idempotency_key,
            request_event_id=event_id,
            request=request,
            now=now,
        )
        shipment = result.shipment

        # Publishing on a replay is intentional. It repairs a prior PutEvents failure.
        # SQS and the worker are explicitly safe for duplicate request events.
        self.publisher.shipment_requested(
            event_id=shipment.request_event_id,
            shipment_id=shipment.shipment_id,
            request=shipment.request,
            occurred_at=shipment.created_at,
        )
        logger.info(
            "shipment request accepted",
            extra={
                "shipment_id": shipment.shipment_id,
                "event_id": shipment.request_event_id,
            },
        )
        return ShipmentAccepted(
            shipment_id=shipment.shipment_id,
            status=shipment.status,
            idempotency_key=shipment.idempotency_key,
            created_at=shipment.created_at,
        )

    def get(self, shipment_id: str) -> ShipmentView:
        shipment = self.repository.get(shipment_id)
        return ShipmentView(
            shipment_id=shipment.shipment_id,
            partner_reference=shipment.partner_reference,
            status=shipment.status,
            created_at=shipment.created_at,
            updated_at=shipment.updated_at,
            failure_reason=shipment.failure_reason,
        )

