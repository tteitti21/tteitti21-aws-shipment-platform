from __future__ import annotations

import boto3
from botocore.config import Config

from .config import Settings


def boto_client(service: str, settings: Settings):
    return boto3.client(
        service,
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        config=Config(
            retries={"max_attempts": 4, "mode": "standard"},
            user_agent_extra="shipment-event-platform/0.1",
        ),
    )

