from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from shipment_platform.api import create_app
from shipment_platform.models import (
    ShipmentAccepted,
    ShipmentStatus,
    ShipmentView,
)
from shipment_platform.repository import ShipmentNotFoundError


class FakeService:
    def __init__(self) -> None:
        self.created_with = None

    def create(self, *, request, idempotency_key):
        self.created_with = (request, idempotency_key)
        return ShipmentAccepted(
            shipment_id="shp_route",
            status=ShipmentStatus.PENDING,
            idempotency_key=idempotency_key,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    def get(self, shipment_id):
        if shipment_id == "missing":
            raise ShipmentNotFoundError(shipment_id)
        return ShipmentView(
            shipment_id=shipment_id,
            partner_reference="partner-order-123",
            status=ShipmentStatus.DISPATCHED,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_post_route_passes_validated_request_and_idempotency_key(valid_payload):
    service = FakeService()
    client = TestClient(create_app(service))

    response = client.post(
        "/shipments",
        json=valid_payload,
        headers={"Idempotency-Key": "partner-123"},
    )

    assert response.status_code == 202
    assert response.json()["shipment_id"] == "shp_route"
    assert service.created_with[1] == "partner-123"
    assert service.created_with[0].origin.country_code == "FI"


def test_validation_rejects_bad_country_code(valid_payload):
    service = FakeService()
    client = TestClient(create_app(service))
    valid_payload["origin"]["country_code"] = "FIN"

    response = client.post(
        "/shipments",
        json=valid_payload,
        headers={"Idempotency-Key": "partner-123"},
    )

    assert response.status_code == 422
    assert service.created_with is None


def test_validation_requires_idempotency_key(valid_payload):
    response = TestClient(create_app(FakeService())).post(
        "/shipments", json=valid_payload
    )

    assert response.status_code == 422


def test_health_route_is_independent_of_aws():
    response = TestClient(create_app(FakeService())).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_lookup():
    client = TestClient(create_app(FakeService()))

    response = client.get("/shipments/shp_lookup")

    assert response.status_code == 200
    assert response.json()["status"] == "DISPATCHED"


def test_status_lookup_returns_404():
    response = TestClient(create_app(FakeService())).get("/shipments/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "shipment not found"}

