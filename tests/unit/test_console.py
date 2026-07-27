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
        AWS_REGION="eu-north-1",
        CONSOLE_SNS_TOPIC_ARN=(
            "arn:aws:sns:eu-north-1:123456789012:"
            "shipment-event-platform-dev-results"
        ),
    )


class FakeSns:
    def __init__(self, subscriptions=None) -> None:
        self.subscriptions = subscriptions or []
        self.subscribe_calls: list[dict] = []

    def list_subscriptions_by_topic(self, **kwargs):
        assert kwargs["TopicArn"] == (
            "arn:aws:sns:eu-north-1:123456789012:"
            "shipment-event-platform-dev-results"
        )
        return {"Subscriptions": self.subscriptions}

    def subscribe(self, **kwargs):
        self.subscribe_calls.append(kwargs)
        self.subscriptions.append(
            {
                "Protocol": kwargs["Protocol"],
                "Endpoint": kwargs["Endpoint"],
                "SubscriptionArn": "PendingConfirmation",
            }
        )
        return {"SubscriptionArn": "PendingConfirmation"}


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
    assert response.json()["sns_configured"] is True
    assert response.json()["sns_topic_name"] == (
        "shipment-event-platform-dev-results"
    )
    assert "123456789012" not in serialized


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


def test_email_subscription_is_restricted_to_configured_topic():
    sns = FakeSns()
    client = TestClient(
        create_console_app(
            console_settings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
            sns,
        )
    )

    response = client.post(
        "/api/sns/subscriptions",
        json={"email": "operator@example.com"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "PENDING_CONFIRMATION"
    assert sns.subscribe_calls == [
        {
            "TopicArn": (
                "arn:aws:sns:eu-north-1:123456789012:"
                "shipment-event-platform-dev-results"
            ),
            "Protocol": "email",
            "Endpoint": "operator@example.com",
            "ReturnSubscriptionArn": True,
        }
    ]


def test_existing_email_subscription_is_not_requested_twice():
    sns = FakeSns(
        [
            {
                "Protocol": "email",
                "Endpoint": "operator@example.com",
                "SubscriptionArn": "PendingConfirmation",
            }
        ]
    )
    client = TestClient(
        create_console_app(
            console_settings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
            sns,
        )
    )

    response = client.post(
        "/api/sns/subscriptions",
        json={"email": "OPERATOR@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING_CONFIRMATION"
    assert sns.subscribe_calls == []


def test_deleted_email_subscription_can_be_requested_again():
    sns = FakeSns(
        [
            {
                "Protocol": "email",
                "Endpoint": "operator@example.com",
                "SubscriptionArn": "Deleted",
            }
        ]
    )
    client = TestClient(
        create_console_app(
            console_settings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
            sns,
        )
    )

    listed = client.get("/api/sns/subscriptions")
    response = client.post(
        "/api/sns/subscriptions",
        json={"email": "operator@example.com"},
    )

    assert listed.json()["subscriptions"] == [
        {"email": "operator@example.com", "status": "DELETED"}
    ]
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING_CONFIRMATION"
    assert len(sns.subscribe_calls) == 1


def test_subscription_list_returns_only_email_endpoints():
    sns = FakeSns(
        [
            {
                "Protocol": "email",
                "Endpoint": "confirmed@example.com",
                "SubscriptionArn": (
                    "arn:aws:sns:eu-north-1:123456789012:"
                    "shipment-event-platform-dev-results:subscription-id"
                ),
            },
            {
                "Protocol": "sqs",
                "Endpoint": "arn:aws:sqs:eu-north-1:123456789012:other",
                "SubscriptionArn": "other-subscription",
            },
        ]
    )
    client = TestClient(
        create_console_app(
            console_settings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
            sns,
        )
    )

    response = client.get("/api/sns/subscriptions")

    assert response.status_code == 200
    assert response.json()["subscriptions"] == [
        {"email": "confirmed@example.com", "status": "CONFIRMED"}
    ]


def test_subscription_requires_valid_email_and_sns_configuration():
    sns = FakeSns()
    configured_client = TestClient(
        create_console_app(
            console_settings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
            sns,
        )
    )

    invalid = configured_client.post(
        "/api/sns/subscriptions",
        json={"email": "not-an-email"},
    )

    assert invalid.status_code == 422
    assert sns.subscribe_calls == []

    unconfigured_client = TestClient(
        create_console_app(
            ConsoleSettings(),
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ),
        )
    )
    missing_topic = unconfigured_client.get("/api/sns/subscriptions")

    assert missing_topic.status_code == 503
