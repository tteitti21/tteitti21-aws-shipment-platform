from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Path as ApiPath
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shipment_platform.logging import configure_logging


logger = logging.getLogger("shipment_platform.console")
STATIC_DIR = Path(__file__).with_name("console_static")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConsoleSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_url: str = Field(default="", alias="CONSOLE_API_URL")
    token_url: str = Field(default="", alias="CONSOLE_TOKEN_URL")
    client_id: str = Field(default="", alias="CONSOLE_CLIENT_ID")
    client_secret: SecretStr = Field(
        default=SecretStr(""), alias="CONSOLE_CLIENT_SECRET"
    )
    scopes: str = Field(
        default="shipment-api/shipments.write shipment-api/shipments.read",
        alias="CONSOLE_SCOPES",
    )
    aws_region: str = Field(default="eu-north-1", alias="AWS_REGION")
    sns_topic_arn: str = Field(default="", alias="CONSOLE_SNS_TOPIC_ARN")
    request_timeout_seconds: float = Field(
        default=15, ge=1, le=60, alias="CONSOLE_REQUEST_TIMEOUT_SECONDS"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("api_url", "token_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @property
    def configured(self) -> bool:
        values = (
            self.api_url,
            self.token_url,
            self.client_id,
            self.client_secret.get_secret_value(),
        )
        if not all(values):
            return False
        return all(
            urlsplit(url).scheme in {"http", "https"} and urlsplit(url).netloc
            for url in (self.api_url, self.token_url)
        )

    @property
    def sns_configured(self) -> bool:
        parts = self.sns_topic_arn.split(":")
        return (
            len(parts) == 6
            and parts[0] == "arn"
            and parts[2] == "sns"
            and parts[3] == self.aws_region
            and bool(parts[4])
            and bool(parts[5])
        )


class ShipmentSubmission(BaseModel):
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    shipment: dict[str, Any]


class TokenResult(BaseModel):
    token_type: str
    expires_at: datetime
    scopes: str


class EmailSubscriptionRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = value.strip()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("enter a valid email address")
        return normalized


class TokenManager:
    def __init__(
        self,
        settings: ConsoleSettings,
        client: httpx.Client,
        *,
        refresh_skew_seconds: int = 30,
    ) -> None:
        self.settings = settings
        self.client = client
        self.refresh_skew_seconds = refresh_skew_seconds
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_epoch = 0.0
        self._token_type = "Bearer"

    def status(self) -> dict[str, Any]:
        usable = bool(
            self._access_token
            and time.time() < self._expires_epoch - self.refresh_skew_seconds
        )
        return {
            "has_usable_token": usable,
            "expires_at": (
                datetime.fromtimestamp(self._expires_epoch, tz=UTC).isoformat()
                if self._expires_epoch
                else None
            ),
            "scopes": self.settings.scopes,
        }

    def invalidate(self) -> None:
        with self._lock:
            self._access_token = None
            self._expires_epoch = 0.0

    def get(self, *, force_refresh: bool = False) -> tuple[str, TokenResult]:
        self._require_configuration()
        with self._lock:
            if (
                not force_refresh
                and self._access_token
                and time.time()
                < self._expires_epoch - self.refresh_skew_seconds
            ):
                return self._access_token, self._result()

            try:
                response = self.client.post(
                    self.settings.token_url,
                    auth=httpx.BasicAuth(
                        self.settings.client_id,
                        self.settings.client_secret.get_secret_value(),
                    ),
                    data={
                        "grant_type": "client_credentials",
                        "scope": self.settings.scopes,
                    },
                )
            except httpx.HTTPError as error:
                logger.warning("Cognito token endpoint could not be reached")
                raise HTTPException(
                    status_code=502,
                    detail="Cognito token endpoint could not be reached",
                ) from error

            if response.status_code >= 400:
                logger.warning(
                    "Cognito token request rejected",
                    extra={"request_id": response.headers.get("x-amzn-requestid")},
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Cognito token request failed with HTTP {response.status_code}",
                )

            try:
                payload = response.json()
                access_token = payload["access_token"]
                expires_in = int(payload["expires_in"])
                token_type = str(payload.get("token_type", "Bearer"))
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=502,
                    detail="Cognito returned an invalid token response",
                ) from error

            if not isinstance(access_token, str) or not access_token:
                raise HTTPException(
                    status_code=502,
                    detail="Cognito returned an invalid access token",
                )

            self._access_token = access_token
            self._expires_epoch = time.time() + max(1, expires_in)
            self._token_type = token_type
            logger.info("M2M access token refreshed")
            return access_token, self._result()

    def _result(self) -> TokenResult:
        return TokenResult(
            token_type=self._token_type,
            expires_at=datetime.fromtimestamp(self._expires_epoch, tz=UTC),
            scopes=self.settings.scopes,
        )

    def _require_configuration(self) -> None:
        if not self.settings.configured:
            raise HTTPException(
                status_code=503,
                detail="Console is not configured; set the CONSOLE_* environment variables",
            )


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"detail": "Upstream service returned a non-JSON response"}


def create_console_app(
    settings: ConsoleSettings | None = None,
    http_client: httpx.Client | None = None,
    sns_client: Any | None = None,
) -> FastAPI:
    settings = settings or ConsoleSettings()
    configure_logging(settings.log_level)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=settings.request_timeout_seconds)
    token_manager = TokenManager(settings, client)
    active_sns_client = sns_client

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if owns_client:
            client.close()

    application = FastAPI(
        title="Shipment Test Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.token_manager = token_manager

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "configured": settings.configured,
            "api_url": settings.api_url,
            "token_host": urlsplit(settings.token_url).hostname,
            "client_id_suffix": (
                settings.client_id[-6:] if len(settings.client_id) >= 6 else ""
            ),
            "sns_configured": settings.sns_configured,
            "sns_topic_name": (
                settings.sns_topic_arn.rsplit(":", 1)[-1]
                if settings.sns_configured
                else ""
            ),
            **token_manager.status(),
        }

    @application.post("/api/token/refresh", response_model=TokenResult)
    def refresh_token() -> TokenResult:
        _token, result = token_manager.get(force_refresh=True)
        return result

    def call_api(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> JSONResponse:
        token, _token_result = token_manager.get()
        headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        def send(access_token: str) -> httpx.Response:
            headers["Authorization"] = f"Bearer {access_token}"
            try:
                return client.request(
                    method,
                    f"{settings.api_url}{path}",
                    headers=headers,
                    json=body,
                )
            except httpx.HTTPError as error:
                raise HTTPException(
                    status_code=502,
                    detail="Shipment API could not be reached",
                ) from error

        response = send(token)
        if response.status_code == 401:
            refreshed_token, _result = token_manager.get(force_refresh=True)
            response = send(refreshed_token)

        return JSONResponse(
            status_code=response.status_code,
            content=_safe_json(response),
        )

    @application.post("/api/shipments")
    def create_shipment(submission: ShipmentSubmission) -> JSONResponse:
        response = call_api(
            "POST",
            "/shipments",
            body=submission.shipment,
            idempotency_key=submission.idempotency_key,
        )
        try:
            shipment_id = json.loads(response.body).get("shipment_id")
        except (AttributeError, TypeError, ValueError):
            shipment_id = None
        logger.info(
            "console submitted shipment",
            extra={"shipment_id": shipment_id},
        )
        return response

    @application.get("/api/shipments/{shipment_id}")
    def get_shipment(
        shipment_id: str = ApiPath(
            min_length=5,
            max_length=80,
            pattern=r"^shp_[A-Za-z0-9]+$",
        ),
    ) -> JSONResponse:
        response = call_api("GET", f"/shipments/{shipment_id}")
        logger.info("console looked up shipment", extra={"shipment_id": shipment_id})
        return response

    def get_sns_client():
        nonlocal active_sns_client
        if not settings.sns_configured:
            raise HTTPException(
                status_code=503,
                detail="SNS controls are not configured for this console",
            )
        if active_sns_client is None:
            active_sns_client = boto3.client("sns", region_name=settings.aws_region)
        return active_sns_client

    def email_subscriptions() -> list[dict[str, str]]:
        subscriptions: list[dict[str, str]] = []
        next_token: str | None = None
        sns = get_sns_client()
        try:
            while True:
                arguments = {"TopicArn": settings.sns_topic_arn}
                if next_token:
                    arguments["NextToken"] = next_token
                response = sns.list_subscriptions_by_topic(**arguments)
                for subscription in response.get("Subscriptions", []):
                    if subscription.get("Protocol") != "email":
                        continue
                    subscription_arn = subscription.get(
                        "SubscriptionArn", "PendingConfirmation"
                    )
                    if subscription_arn == "PendingConfirmation":
                        status = "PENDING_CONFIRMATION"
                    elif subscription_arn == "Deleted":
                        status = "DELETED"
                    else:
                        status = "CONFIRMED"
                    subscriptions.append(
                        {
                            "email": subscription.get("Endpoint", ""),
                            "status": status,
                        }
                    )
                next_token = response.get("NextToken")
                if not next_token:
                    break
        except (BotoCoreError, ClientError) as error:
            logger.warning("SNS subscriptions could not be listed")
            raise HTTPException(
                status_code=502,
                detail="SNS subscriptions could not be listed",
            ) from error
        return subscriptions

    @application.get("/api/sns/subscriptions")
    def list_sns_subscriptions() -> dict[str, Any]:
        return {
            "topic": settings.sns_topic_arn.rsplit(":", 1)[-1],
            "subscriptions": email_subscriptions(),
        }

    @application.post("/api/sns/subscriptions")
    def create_sns_subscription(
        request: EmailSubscriptionRequest,
    ) -> JSONResponse:
        existing = next(
            (
                subscription
                for subscription in email_subscriptions()
                if (
                    subscription["email"].casefold() == request.email.casefold()
                    and subscription["status"] != "DELETED"
                )
            ),
            None,
        )
        if existing:
            return JSONResponse(
                status_code=200,
                content={
                    **existing,
                    "message": (
                        "Subscription already exists. Check your email if "
                        "confirmation is still pending."
                    ),
                },
            )

        try:
            response = get_sns_client().subscribe(
                TopicArn=settings.sns_topic_arn,
                Protocol="email",
                Endpoint=request.email,
                ReturnSubscriptionArn=True,
            )
        except (BotoCoreError, ClientError) as error:
            logger.warning("SNS email subscription request failed")
            raise HTTPException(
                status_code=502,
                detail="SNS email subscription request failed",
            ) from error

        subscription_arn = response.get(
            "SubscriptionArn", "PendingConfirmation"
        )
        status = (
            "PENDING_CONFIRMATION"
            if subscription_arn == "PendingConfirmation"
            else "CONFIRMED"
        )
        logger.info("SNS email subscription requested")
        return JSONResponse(
            status_code=202 if status == "PENDING_CONFIRMATION" else 200,
            content={
                "email": request.email,
                "status": status,
                "message": (
                    "AWS sent a confirmation email. The subscription receives "
                    "events only after its confirmation link is opened."
                ),
            },
        )

    application.mount(
        "/assets",
        StaticFiles(directory=STATIC_DIR),
        name="console-assets",
    )
    return application


app = create_console_app()
