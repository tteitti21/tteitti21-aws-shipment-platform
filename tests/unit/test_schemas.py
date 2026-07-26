from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).parents[2]
SCHEMA_DIR = ROOT / "schemas"
EXAMPLE_DIR = ROOT / "examples"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema_registry() -> tuple[Registry, dict[str, dict]]:
    schemas = {
        path.name: load_json(path) for path in SCHEMA_DIR.glob("*.schema.json")
    }
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(
            schema["$id"], Resource.from_contents(schema)
        )
    return registry, schemas


def test_create_request_example_matches_schema(schema_registry):
    registry, schemas = schema_registry
    Draft202012Validator(
        schemas["create-shipment.schema.json"],
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(load_json(EXAMPLE_DIR / "create-shipment.json"))


@pytest.mark.parametrize(
    ("event_file", "schema_file"),
    [
        ("shipment-requested.event.json", "shipment-requested.schema.json"),
        ("shipment-dispatched.event.json", "shipment-dispatched.schema.json"),
        ("shipment-failed.event.json", "shipment-failed.schema.json"),
    ],
)
def test_event_example_detail_matches_schema(
    schema_registry, event_file, schema_file
):
    registry, schemas = schema_registry
    event = load_json(EXAMPLE_DIR / event_file)
    Draft202012Validator(
        schemas[schema_file],
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(event["detail"])

