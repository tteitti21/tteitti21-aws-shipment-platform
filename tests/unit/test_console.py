from __future__ import annotations

import base64
import json

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from shipment_platform.console import ConsoleSettings, create_console_app


def console_settings() -> ConsoleSettings:
    return ConsoleSettings(
        CONSOLE_API_URL="https://api.example.test",
        CONSOLE_TOKEN_URL="https://auth.example.test/oauth2/token",
        CONSOLE_CLIENT_ID="client-123456",
        CONSOLE_CLIENT_SECRET=SecretStr("super-secret"),
        CONSOLE_SCOPES=(
            "shipment-api/shipments.write shipment-api/shipments.read"
        ),
    )


def test_configuration_never_exposes_secret_or_access_token():
    client = TestClient(
        create_console_app(
            console_settings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
        )
    )

    response = client.get("/api/config")

    assert response.status_code == 200
    serialized = response.text
    assert "super-secret" not in serialized
    assert "access_token" not in serialized
    assert response.json()["client_id_suffix"] == "123456"


def test_refresh_token_uses_basic_auth_but_returns_only_metadata():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
        observed["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "private-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    upstream = httpx.Client(transport=httpx.MockTransport(handler))
    client = TestClient(create_console_app(console_settings(), upstream))

    response = client.post("/api/token/refresh")

    assert response.status_code == 200
    assert "private-token" not in response.text
    expected = base64.b64encode(b"client-123456:super-secret").decode()
    assert observed["authorization"] == f"Basic {expected}"
    assert "grant_type=client_credentials" in observed["body"]
    assert "shipment-api%2Fshipments.write" in observed["body"]


def test_submit_gets_token_and_forwards_bearer_body_and_idempotency_key(
    valid_payload,
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "auth.example.test":
            return httpx.Response(
                200,
                json={
                    "access_token": "server-only-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(
            202,
            json={
                "shipment_id": "shp_console123",
                "status": "PENDING",
                "idempotency_key": "console-key-123",
                "created_at": "2026-07-27T10:00:00Z",
            },
        )

    upstream = httpx.Client(transport=httpx.MockTransport(handler))
    client = TestClient(create_console_app(console_settings(), upstream))

    response = client.post(
        "/api/shipments",
        json={
            "idempotency_key": "console-key-123",
            "shipment": valid_payload,
        },
    )

    assert response.status_code == 202
    assert response.json()["shipment_id"] == "shp_console123"
    api_request = requests[1]
    assert api_request.headers["Authorization"] == "Bearer server-only-token"
    assert api_request.headers["Idempotency-Key"] == "console-key-123"
    assert json.loads(api_request.content) == valid_payload


def test_status_lookup_uses_cached_token():
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.host == "auth.example.test":
            token_requests += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "cached-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        assert request.headers["Authorization"] == "Bearer cached-token"
        return httpx.Response(
            200,
            json={
                "shipment_id": "shp_console123",
                "partner_reference": "partner-order-123",
                "status": "DISPATCHED",
                "created_at": "2026-07-27T10:00:00Z",
                "updated_at": "2026-07-27T10:00:01Z",
                "failure_reason": None,
            },
        )

    upstream = httpx.Client(transport=httpx.MockTransport(handler))
    client = TestClient(create_console_app(console_settings(), upstream))

    first = client.get("/api/shipments/shp_console123")
    second = client.get("/api/shipments/shp_console123")

    assert first.status_code == 200
    assert second.json()["status"] == "DISPATCHED"
    assert token_requests == 1


def test_unconfigured_console_fails_without_exposing_details(valid_payload):
    client = TestClient(
        create_console_app(
            ConsoleSettings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
        )
    )

    response = client.post(
        "/api/shipments",
        json={"idempotency_key": "console-key", "shipment": valid_payload},
    )

    assert response.status_code == 503
    assert response.json()["detail"].startswith("Console is not configured")


def test_cognito_rejection_is_sanitized():
    upstream = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={"error": "invalid_client", "secret": "do-not-relay"},
            )
        )
    )
    client = TestClient(create_console_app(console_settings(), upstream))

    response = client.post("/api/token/refresh")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Cognito token request failed with HTTP 401"
    }
    assert "do-not-relay" not in response.text
