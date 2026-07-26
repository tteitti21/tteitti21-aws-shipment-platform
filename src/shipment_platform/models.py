from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from typing_extensions import Annotated


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CountryCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{2}$")
]


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    address_line1: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    postal_code: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)
    ]
    country_code: CountryCode


class Package(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight_kg: Decimal = Field(gt=Decimal("0"), le=Decimal("1000"), max_digits=7)
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]


class ShipmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    origin: Address
    destination: Address
    packages: list[Package] = Field(min_length=1, max_length=50)

    @field_validator("destination")
    @classmethod
    def different_addresses(cls, destination: Address, info):
        origin = info.data.get("origin")
        if origin is not None and destination == origin:
            raise ValueError("origin and destination must be different")
        return destination


class ShipmentStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"


class ShipmentAccepted(BaseModel):
    shipment_id: str
    status: ShipmentStatus
    idempotency_key: str
    created_at: datetime


class ShipmentView(BaseModel):
    shipment_id: str
    partner_reference: str
    status: ShipmentStatus
    created_at: datetime
    updated_at: datetime
    failure_reason: str | None = None


class ShipmentRecord(ShipmentView):
    idempotency_key: str
    request: ShipmentCreate
    request_event_id: str
    result_event_id: str | None = None
    result_event_published: bool = False


class EventDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    shipment_id: str
    occurred_at: datetime


class ShipmentRequestedDetail(EventDetail):
    partner_reference: str
    request: ShipmentCreate


class ShipmentDispatchedDetail(EventDetail):
    request_event_id: str
    status: ShipmentStatus = ShipmentStatus.DISPATCHED


class ShipmentFailedDetail(EventDetail):
    request_event_id: str
    status: ShipmentStatus = ShipmentStatus.FAILED
    reason: str


class EventBridgeEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    source: str
    detail_type: str = Field(alias="detail-type")
    detail: ShipmentRequestedDetail

