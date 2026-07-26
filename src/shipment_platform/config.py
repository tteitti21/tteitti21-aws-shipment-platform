from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aws_region: str = Field(default="eu-north-1", alias="AWS_REGION")
    aws_endpoint_url: str | None = Field(default=None, alias="AWS_ENDPOINT_URL")
    table_name: str = Field(default="shipment-event-platform-dev", alias="TABLE_NAME")
    event_bus_name: str = Field(
        default="shipment-event-platform-dev", alias="EVENT_BUS_NAME"
    )
    processing_queue_url: str = Field(
        default="http://localhost:4566/000000000000/shipments",
        alias="PROCESSING_QUEUE_URL",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    sqs_wait_time_seconds: int = Field(
        default=20, ge=1, le=20, alias="SQS_WAIT_TIME_SECONDS"
    )
    sqs_visibility_timeout_seconds: int = Field(
        default=120, ge=30, le=43200, alias="SQS_VISIBILITY_TIMEOUT_SECONDS"
    )
    shipment_retention_days: int = Field(
        default=30, ge=1, le=3650, alias="SHIPMENT_RETENTION_DAYS"
    )

    @field_validator("aws_endpoint_url", mode="before")
    @classmethod
    def empty_endpoint_is_none(cls, value):
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
