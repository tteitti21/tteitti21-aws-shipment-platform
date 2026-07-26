from __future__ import annotations

import logging
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .aws import boto_client
from .config import Settings, get_settings
from .events import EventPublishError, EventPublisher
from .logging import configure_logging
from .models import ShipmentAccepted, ShipmentCreate, ShipmentView
from .repository import (
    IdempotencyConflictError,
    ShipmentNotFoundError,
    ShipmentRepository,
)
from .service import ShipmentService


logger = logging.getLogger(__name__)


def build_service(settings: Settings) -> ShipmentService:
    return ShipmentService(
        repository=ShipmentRepository(
            boto_client("dynamodb", settings),
            settings.table_name,
            settings.shipment_retention_days,
        ),
        publisher=EventPublisher(
            boto_client("events", settings), settings.event_bus_name
        ),
    )


def create_app(service: ShipmentService | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Shipment Event Platform",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if service is not None:
        app.state.shipment_service = service

    def shipment_service() -> ShipmentService:
        if not hasattr(app.state, "shipment_service"):
            app.state.shipment_service = build_service(settings)
        return app.state.shipment_service

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        response = await call_next(request)
        logger.info(
            "http request",
            extra={"request_id": request.headers.get("x-amzn-trace-id")},
        )
        return response

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/shipments",
        response_model=ShipmentAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_shipment(
        body: ShipmentCreate,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ],
    ) -> ShipmentAccepted:
        try:
            return shipment_service().create(
                request=body, idempotency_key=idempotency_key
            )
        except IdempotencyConflictError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key was already used for a different request",
            ) from None
        except EventPublishError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="shipment was stored but event publication failed; retry with the same Idempotency-Key",
            ) from None

    @app.get("/shipments/{shipment_id}", response_model=ShipmentView)
    def get_shipment(shipment_id: str) -> ShipmentView:
        try:
            return shipment_service().get(shipment_id)
        except ShipmentNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="shipment not found",
            ) from None

    @app.exception_handler(Exception)
    async def unhandled_error(_request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled request error", exc_info=error)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error"},
        )

    return app


app = create_app()

