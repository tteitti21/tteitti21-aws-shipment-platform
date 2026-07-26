from __future__ import annotations

import json
from datetime import UTC, datetime

from .models import (
    ShipmentCreate,
    ShipmentDispatchedDetail,
    ShipmentFailedDetail,
    ShipmentStatus,
)


API_EVENT_SOURCE = "shipment-event-platform.api"
WORKER_EVENT_SOURCE = "shipment-event-platform.worker"


class EventPublishError(RuntimeError):
    pass


class EventPublisher:
    def __init__(self, events_client, event_bus_name: str) -> None:
        self._client = events_client
        self._event_bus_name = event_bus_name

    def shipment_requested(
        self,
        *,
        event_id: str,
        shipment_id: str,
        request: ShipmentCreate,
        occurred_at: datetime,
    ) -> None:
        detail = {
            "event_id": event_id,
            "shipment_id": shipment_id,
            "occurred_at": occurred_at.isoformat(),
            "partner_reference": request.partner_reference,
            "request": request.model_dump(mode="json"),
        }
        self._put(
            source=API_EVENT_SOURCE,
            detail_type="ShipmentRequested",
            detail=detail,
        )

    def shipment_dispatched(
        self,
        *,
        event_id: str,
        shipment_id: str,
        request_event_id: str,
        occurred_at: datetime | None = None,
    ) -> None:
        detail = ShipmentDispatchedDetail(
            event_id=event_id,
            shipment_id=shipment_id,
            request_event_id=request_event_id,
            occurred_at=occurred_at or datetime.now(UTC),
            status=ShipmentStatus.DISPATCHED,
        )
        self._put(
            source=WORKER_EVENT_SOURCE,
            detail_type="ShipmentDispatched",
            detail=detail.model_dump(mode="json"),
        )

    def shipment_failed(
        self,
        *,
        event_id: str,
        shipment_id: str,
        request_event_id: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> None:
        detail = ShipmentFailedDetail(
            event_id=event_id,
            shipment_id=shipment_id,
            request_event_id=request_event_id,
            occurred_at=occurred_at or datetime.now(UTC),
            status=ShipmentStatus.FAILED,
            reason=reason,
        )
        self._put(
            source=WORKER_EVENT_SOURCE,
            detail_type="ShipmentFailed",
            detail=detail.model_dump(mode="json"),
        )

    def _put(self, *, source: str, detail_type: str, detail: dict) -> None:
        response = self._client.put_events(
            Entries=[
                {
                    "EventBusName": self._event_bus_name,
                    "Source": source,
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail, separators=(",", ":")),
                }
            ]
        )
        if response.get("FailedEntryCount", 0):
            entry = response.get("Entries", [{}])[0]
            raise EventPublishError(
                f"{entry.get('ErrorCode', 'UnknownError')}: "
                f"{entry.get('ErrorMessage', 'PutEvents failed')}"
            )

