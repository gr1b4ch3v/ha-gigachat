"""Tests for the conversion of Home Assistant tools to GigaChat functions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
import pytest
import voluptuous as vol

from custom_components.gigachat.tools import (
    format_schema,
    format_structure,
    format_tool,
    parse_tool_args,
    sanitize_function_name,
)

# Tool schemas produced by the Assist API of Home Assistant 2026.9
ASSIST_TOOLS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "assist_tools.json").read_text()
)

DEVICE_CLASSES = ["outlet", "switch", "tv", "speaker", "receiver", "projector"]


def _walk(schema: dict[str, Any], key: str | None = None) -> None:
    """Assert that a schema only uses constructs GigaChat accepts."""
    assert set(schema) <= {
        "type",
        "description",
        "properties",
        "items",
        "enum",
        "required",
        "minimum",
        "maximum",
    }
    assert schema["type"] in {
        "string",
        "number",
        "integer",
        "boolean",
        "array",
        "object",
    }
    if key is not None:
        assert schema["description"]
    for name, value in schema.get("properties", {}).items():
        _walk(value, name)
    if "items" in schema:
        _walk(schema["items"])


@pytest.mark.parametrize("tool", ASSIST_TOOLS, ids=lambda tool: tool["name"])
def test_format_assist_tool_schemas(tool: dict[str, Any]) -> None:
    """Test that the Assist tool schemas are converted to the supported subset."""
    schema = format_schema(tool["parameters"])
    _walk(schema)
    assert set(schema["properties"]) == set(tool["parameters"]["properties"])


def test_format_schema_any_of() -> None:
    """Test that anyOf is replaced by its first concrete option."""
    schema = format_schema(
        {
            "type": "object",
            "properties": {
                "domain": {
                    "anyOf": [
                        {"type": "null"},
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Filter by domain",
                },
                "device_class": {
                    "type": "array",
                    "items": {"type": "string", "enum": DEVICE_CLASSES},
                },
            },
            "required": ["domain", "missing"],
            "additionalProperties": False,
        }
    )
    assert schema == {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "Filter by domain"},
            "device_class": {
                "type": "array",
                "description": "Device class of the target entities. Omit unless "
                "the user explicitly mentions it",
                "items": {"type": "string", "enum": DEVICE_CLASSES},
            },
        },
        "required": ["domain"],
    }


def test_format_schema_all_of_and_types() -> None:
    """Test allOf merging and type inference."""
    schema = format_schema(
        {
            "allOf": [
                {"properties": {"a": {"type": ["integer", "null"]}}, "required": ["a"]},
                {"properties": {"b": {"enum": [1, 2]}, "c": {"enum": [True]}}},
            ]
        }
    )
    assert schema["type"] == "object"
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"] == {
        "type": "integer",
        "description": "B",
        "enum": [1, 2],
    }
    assert schema["properties"]["c"]["type"] == "boolean"
    assert schema["required"] == ["a"]
    assert (
        format_schema({"type": "number", "enum": [0.5], "minimum": 0})["type"]
        == "number"
    )
    assert format_schema({"enum": [1.5]})["type"] == "number"
    assert format_schema({"items": {}})["type"] == "array"
    assert format_schema("oops", "name")["type"] == "string"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("intent__HassTurnOn", "intent__HassTurnOn"),
        ("script.morning-routine", "script_morning_routine"),
        ("1st_tool", "f_1st_tool"),
        ("_private", "f__private"),
        ("", "f_"),
    ],
)
def test_sanitize_function_name(name: str, expected: str) -> None:
    """Test function name sanitizing."""
    assert sanitize_function_name(name) == expected


def test_parse_tool_args() -> None:
    """Test the cleanup of generated arguments."""
    parameters = format_schema(ASSIST_TOOLS[1]["parameters"])  # HassTurnOn
    degenerate = DEVICE_CLASSES * 3 + ["awning", "blind", "curtain", "damper", "door"]
    assert parse_tool_args(
        {
            "name": " Свет на кухне ",
            "area": "",
            "floor": None,
            "domain": ["light", "light"],
            "device_class": degenerate,
        },
        parameters,
    ) == {"name": "Свет на кухне", "domain": ["light"]}
    # A short, real device class filter is kept
    assert parse_tool_args({"device_class": ["tv"]}, parameters) == {
        "device_class": ["tv"]
    }
    # JSON strings are decoded
    assert parse_tool_args('{"name": "x", "domain": "[\\"light\\"]"}') == {
        "name": "x",
        "domain": ["light"],
    }
    assert parse_tool_args("") == {}
    assert parse_tool_args("not json") == {}
    assert parse_tool_args(["list"]) == {}
    assert parse_tool_args({"flag": False, "count": 0}) == {"flag": False, "count": 0}


def test_format_structure() -> None:
    """Test the response_format schema for structured AI tasks."""
    structure = vol.Schema(
        {vol.Required("condition"): str, vol.Optional("temperature"): vol.Coerce(float)}
    )
    schema = format_structure(structure, llm.selector_serializer)
    assert schema["type"] == "object"
    assert schema["required"] == ["condition", "temperature"]
    assert schema["properties"]["condition"]["type"] == "string"


async def test_format_tool(hass: HomeAssistant) -> None:
    """Test formatting a tool defined with voluptuous."""

    class MyTool(llm.Tool):
        name = "script.lights-off"
        description = ""
        parameters = vol.Schema(
            {
                vol.Required("area"): str,
                vol.Optional("mode"): vol.Any("fast", "slow"),
            }
        )

        async def async_call(self, *args: Any) -> dict:
            return {}

    spec = format_tool(MyTool(), llm.selector_serializer)
    assert spec["name"] == "script_lights_off"
    assert spec["description"] == "Script.lights-off"
    _walk(spec["parameters"])
    assert spec["parameters"]["required"] == ["area"]
