from __future__ import annotations

import pytest

from shipment_platform.repository import IdempotencyConflictError
from shipment_platform.service import ShipmentService


def test_same_idempotency_key_returns_original_shipment(
    repository, recording_publisher, valid_request
):
    service = ShipmentService(repository, recording_publisher)

    first = service.create(request=valid_request, idempotency_key="same-key")
    replay = service.create(request=valid_request, idempotency_key="same-key")

    assert replay.shipment_id == first.shipment_id
    assert replay.created_at == first.created_at
    # Re-publication repairs the API/database dual-write failure window.
    assert len(recording_publisher.requested) == 2
    assert (
        recording_publisher.requested[0]["event_id"]
        == recording_publisher.requested[1]["event_id"]
    )


def test_idempotency_key_cannot_be_reused_for_different_payload(
    repository, recording_publisher, valid_request, valid_payload
):
    service = ShipmentService(repository, recording_publisher)
    service.create(request=valid_request, idempotency_key="conflict-key")
    valid_payload["partner_reference"] = "different-order"
    changed_request = type(valid_request).model_validate(valid_payload)

    with pytest.raises(IdempotencyConflictError):
        service.create(request=changed_request, idempotency_key="conflict-key")

