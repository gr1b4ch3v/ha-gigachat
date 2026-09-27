"""Convert Home Assistant LLM tools to GigaChat functions and back.

GigaChat only understands a subset of JSON Schema: every property needs a
`type` and a `description`, and combinators such as `anyOf` are rejected with
HTTP 422. Function names may only contain Latin letters, digits and
underscores and must not start with a digit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import re
from typing import Any

from homeassistant.helpers import llm

try:
    from probatio import to_openapi
except ImportError:  # Home Assistant before the probatio migration
    from voluptuous_openapi import convert as to_openapi

# Descriptions for common Assist intent slots that come without one.
SLOT_DESCRIPTIONS: dict[str, str] = {
    "name": "Name or alias of the device or entity, exactly as listed in the smart home overview",
    "area": "Name or alias of the area (room)",
    "floor": "Name or alias of the floor",
    "domain": "Home Assistant domain of the target entities, for example light, switch or cover",
    "device_class": "Device class of the target entities. Omit unless the user explicitly mentions it",
    "color": "Color name, for example red or warm white",
    "temperature": "Temperature value",
    "brightness": "Brightness in percent from 0 to 100",
    "position": "Position in percent from 0 to 100",
    "volume_level": "Volume level in percent from 0 to 100",
    "item": "Text of the item",
    "duration": "Duration",
    "hours": "Number of hours",
    "minutes": "Number of minutes",
    "seconds": "Number of seconds",
    "start_hours": "Hours of the original timer duration",
    "start_minutes": "Minutes of the original timer duration",
    "start_seconds": "Seconds of the original timer duration",
    "conversation_command": "Command to execute when the timer finishes",
    "search_query": "What to search for",
    "media_class": "Type of media, for example album, artist or track",
    "status": "Status of the item",
    "response": "Text response",
}

_JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object"})
_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_]")


def sanitize_function_name(name: str) -> str:
    """Return a function name that GigaChat accepts."""
    sanitized = _INVALID_NAME_CHARS.sub("_", name)
    if not sanitized or not sanitized[0].isalpha():
        sanitized = f"f_{sanitized}"
    return sanitized


def humanize(key: str) -> str:
    """Turn a property key into a readable description."""
    return key.replace("_", " ").strip().capitalize() or key


def format_schema(schema: Any, key: str | None = None) -> dict[str, Any]:
    """Rewrite a JSON schema into the subset understood by GigaChat."""
    if not isinstance(schema, Mapping):
        return {"type": "string", "description": humanize(key)} if key else {}

    schema = _resolve_combinators(schema)

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if t != "null"), "string")
    if schema_type not in _JSON_TYPES:
        schema_type = _infer_type(schema)

    result: dict[str, Any] = {"type": schema_type}
    if description := schema.get("description"):
        result["description"] = str(description)
    elif key is not None:
        result["description"] = SLOT_DESCRIPTIONS.get(key, humanize(key))

    if isinstance(enum := schema.get("enum"), list) and enum:
        result["enum"] = [value for value in enum if value is not None]

    if schema_type in ("number", "integer"):
        for bound in ("minimum", "maximum"):
            if isinstance(schema.get(bound), (int, float)):
                result[bound] = schema[bound]
    elif schema_type == "array":
        items = format_schema(schema.get("items") or {"type": "string"})
        items.pop("description", None)
        result["items"] = items
    elif schema_type == "object":
        properties = schema.get("properties")
        result["properties"] = {
            str(name): format_schema(value, str(name))
            for name, value in (properties or {}).items()
        }
        required = [
            name
            for name in schema.get("required") or ()
            if name in result["properties"]
        ]
        if required:
            result["required"] = required
    return result


def format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> dict[str, Any]:
    """Format an LLM tool as a GigaChat function specification."""
    parameters = format_schema(
        to_openapi(tool.parameters, custom_serializer=custom_serializer)
    )
    if parameters.get("type") != "object":
        parameters = {"type": "object", "properties": {}}
    parameters.pop("description", None)
    name = sanitize_function_name(tool.name)
    return {
        "name": name,
        "description": tool.description or humanize(tool.name),
        "parameters": parameters,
    }


def format_structure(
    structure: Any, custom_serializer: Callable[[Any], Any] | None
) -> dict[str, Any]:
    """Format a voluptuous structure as a response_format JSON schema."""
    schema = format_schema(to_openapi(structure, custom_serializer=custom_serializer))
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    schema.pop("description", None)
    # strict mode only guarantees the listed fields, so require all of them.
    schema["required"] = list(schema.get("properties", {}))
    return schema


def parse_tool_args(
    arguments: Any, parameters: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Clean up the arguments generated by the model for a function call.

    GigaChat returns an object (the docs claim a JSON string, so accept both),
    likes to fill optional parameters with empty values and some models repeat
    enum values endlessly in array parameters. Those values would make the
    Assist intents fail to match, so they are dropped.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            return {}
    if not isinstance(arguments, Mapping):
        return {}

    properties: Mapping[str, Any] = (parameters or {}).get("properties") or {}
    cleaned: dict[str, Any] = {}
    for key, raw_value in arguments.items():
        value = _fix_json_value(raw_value)
        if value is None or value in ("", [], {}):
            continue
        if isinstance(value, list):
            value = _dedupe(value)
            enum = ((properties.get(key) or {}).get("items") or {}).get("enum")
            if enum and len(value) > 2 and len(value) * 2 > len(enum):
                # Listing (almost) every allowed value is not a real filter.
                continue
        cleaned[key] = value
    return cleaned


def _resolve_combinators(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    """Replace anyOf/oneOf/allOf by a single concrete schema."""
    for combinator in ("anyOf", "oneOf"):
        if isinstance(options := schema.get(combinator), list):
            concrete = [
                option
                for option in options
                if isinstance(option, Mapping) and option.get("type") != "null"
            ]
            merged = dict(concrete[0]) if concrete else {"type": "string"}
            for field in ("description", "enum"):
                if field in schema and field not in merged:
                    merged[field] = schema[field]
            return _resolve_combinators(merged)
    if isinstance(parts := schema.get("allOf"), list):
        merged = {k: v for k, v in schema.items() if k != "allOf"}
        for raw_part in parts:
            if not isinstance(raw_part, Mapping):
                continue
            part = _resolve_combinators(raw_part)
            if "properties" in part:
                merged.setdefault("properties", {}).update(part["properties"])
                merged["required"] = [
                    *merged.get("required", []),
                    *part.get("required", []),
                ]
            for field, value in part.items():
                if field not in ("properties", "required"):
                    merged.setdefault(field, value)
        return merged
    return schema


def _infer_type(schema: Mapping[str, Any]) -> str:
    """Guess the type of a schema that does not declare a valid one."""
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    if isinstance(enum := schema.get("enum"), list) and enum:
        first = enum[0]
        if isinstance(first, bool):
            return "boolean"
        if isinstance(first, int):
            return "integer"
        if isinstance(first, float):
            return "number"
    return "string"


def _fix_json_value(value: Any) -> Any:
    """Decode arguments that were returned as JSON encoded strings."""
    if isinstance(value, str):
        stripped = value.strip()
        if (stripped.startswith("[") and stripped.endswith("]")) or (
            stripped.startswith("{") and stripped.endswith("}")
        ):
            try:
                return json.loads(stripped)
            except ValueError:
                pass
        return stripped
    return value


def _dedupe(values: list[Any]) -> list[Any]:
    """Remove duplicate and empty list items, keeping the original order."""
    result: list[Any] = []
    for value in values:
        if value in (None, "") or value in result:
            continue
        result.append(value)
    return result
