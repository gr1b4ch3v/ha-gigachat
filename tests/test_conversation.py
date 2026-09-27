"""Tests for the GigaChat conversation agent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent, llm
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gigachat.api import (
    GigaChatAuthError,
    GigaChatConnectionError,
    GigaChatPaymentRequiredError,
    GigaChatRateLimitError,
    GigaChatRequestError,
)
from custom_components.gigachat.const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_RECOMMENDED,
    CONF_TEMPERATURE,
)

from .common import function_call_chunks, mock_stream, text_chunks

AGENT_ID = "conversation.gigachat"


async def _converse(
    hass: HomeAssistant,
    text: str,
    conversation_id: str | None = None,
    language: str = "en",
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass, text, conversation_id, Context(), language=language, agent_id=AGENT_ID
    )


async def test_text_answer(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test a plain streamed answer."""
    mock_client.async_chat_stream, payloads = mock_stream(text_chunks("Hel", "lo!"))

    result = await _converse(hass, "Hi")

    assert result.response.response_type is intent.IntentResponseType.ACTION_DONE
    assert result.response.speech["plain"]["speech"] == "Hello!"
    payload = payloads[0]
    assert payload["model"] == "GigaChat-3-Ultra"
    assert payload["function_call"] == "auto"
    assert {f["name"] for f in payload["functions"]} >= {"intent__HassTurnOn"}
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "Never use Markdown" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "Hi"}
    assert payload["_session_id"].startswith("ha-")
    assert payload["_read_timeout"] == 60
    assert "temperature" not in payload

    state = hass.states.get(AGENT_ID)
    assert (
        state.attributes["supported_features"]
        == conversation.ConversationEntityFeature.CONTROL
    )


async def test_tool_call_round_trip(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that a function call is executed and its result is sent back."""
    mock_client.async_chat_stream, payloads = mock_stream(
        function_call_chunks(
            "intent__HassTurnOn",
            {"name": "Kitchen light", "area": "", "device_class": []},
            call_id="call-42",
            state_id="state-42",
        ),
        text_chunks("The light is on."),
    )

    with patch(
        "homeassistant.helpers.llm.APIInstance.async_call_tool",
        return_value={"success": True, "result": "done"},
    ) as mock_call_tool:
        result = await _converse(hass, "Turn on the kitchen light")

    assert result.response.speech["plain"]["speech"] == "The light is on."
    tool_input: llm.ToolInput = mock_call_tool.call_args[0][0]
    assert tool_input.id == "call-42"
    assert tool_input.tool_name == "intent__HassTurnOn"
    assert tool_input.tool_args == {"name": "Kitchen light"}  # empty values dropped

    messages = payloads[1]["messages"]
    assert messages[-2] == {
        "role": "assistant",
        "content": "",
        "function_call": {
            "name": "intent__HassTurnOn",
            "arguments": {"name": "Kitchen light"},
        },
        "functions_state_id": "state-42",
    }
    assert messages[-1]["role"] == "function"
    assert messages[-1]["name"] == "intent__HassTurnOn"
    assert json.loads(messages[-1]["content"]) == {"success": True, "result": "done"}


async def test_tool_result_not_an_object(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that non-object tool results are wrapped into an object."""
    mock_client.async_chat_stream, payloads = mock_stream(
        function_call_chunks("llm__GetDateTime", "{}"), text_chunks("Noon.")
    )
    with patch(
        "homeassistant.helpers.llm.APIInstance.async_call_tool", return_value=["12:00"]
    ):
        await _converse(hass, "What time is it?")
    assert json.loads(payloads[1]["messages"][-1]["content"]) == {"result": ["12:00"]}


async def test_multi_turn(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that the history is sent with the next request."""
    mock_client.async_chat_stream, payloads = mock_stream(
        text_chunks("Nice to meet you, Alex."), text_chunks("You are Alex.")
    )
    first = await _converse(hass, "I am Alex")
    await _converse(hass, "Who am I?", first.conversation_id)
    assert [(m["role"], m["content"]) for m in payloads[1]["messages"][1:]] == [
        ("user", "I am Alex"),
        ("assistant", "Nice to meet you, Alex."),
        ("user", "Who am I?"),
    ]


@pytest.mark.parametrize(
    "conversation_options",
    [
        {
            CONF_RECOMMENDED: False,
            CONF_CHAT_MODEL: "GigaChat-2",
            CONF_TEMPERATURE: 0.2,
            CONF_MAX_TOKENS: 100,
        }
    ],
)
async def test_custom_parameters_without_tools(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test sampling parameters and an agent without Home Assistant control."""
    mock_client.async_chat_stream, payloads = mock_stream(text_chunks("Ok"))
    await _converse(hass, "Hi")
    payload = payloads[0]
    assert payload["model"] == "GigaChat-2"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 100
    assert "functions" not in payload
    assert not hass.states.get(AGENT_ID).attributes.get("supported_features")


@pytest.mark.parametrize(
    ("error", "language", "expected"),
    [
        (
            GigaChatPaymentRequiredError("Payment Required", 402),
            "en",
            "The token package of GigaChat-3-Ultra is used up",
        ),
        (
            GigaChatPaymentRequiredError("Payment Required", 402),
            "ru",
            "Пакет токенов модели GigaChat-3-Ultra израсходован",
        ),
        (GigaChatRateLimitError("Too Many Requests", 429), "en", "GigaChat is busy"),
        (
            GigaChatConnectionError("timeout"),
            "ru",
            "Не удалось подключиться к GigaChat",
        ),
        (
            GigaChatRequestError("Model does not support image", 422),
            "en",
            "GigaChat-3-Ultra cannot process images",
        ),
        (GigaChatRequestError("Invalid params", 422), "en", "Invalid params"),
    ],
)
async def test_api_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    error: Exception,
    language: str,
    expected: str,
) -> None:
    """Test that API errors are spoken in the language of the conversation."""
    mock_client.async_chat_stream, _ = mock_stream(error)
    result = await _converse(hass, "Hi", language=language)
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert expected in result.response.speech["plain"]["speech"]


async def test_auth_error_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that rejected credentials start the reauth flow."""
    mock_client.async_chat_stream, _ = mock_stream(
        GigaChatAuthError("Unauthorized", 401)
    )
    result = await _converse(hass, "Hi")
    assert result.response.response_type is intent.IntentResponseType.ERROR
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ([], "empty response"),
        (
            [
                {
                    "choices": [
                        {
                            "delta": {"content": "", "role": "assistant"},
                            "finish_reason": "error",
                        }
                    ]
                }
            ],
            "invalid function call",
        ),
    ],
)
async def test_invalid_responses(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    chunks: list[dict[str, Any]],
    expected: str,
) -> None:
    """Test empty answers and invalid function calls."""
    mock_client.async_chat_stream, _ = mock_stream(chunks)
    result = await _converse(hass, "Hi")
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert expected in result.response.speech["plain"]["speech"]


async def test_progress_events_are_ignored(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that built-in function progress is not shown to the user."""
    progress = {
        "choices": [
            {
                "delta": {
                    "content": "осталось 00:05",
                    "role": "function_in_progress",
                    "name": "text2image",
                }
            }
        ]
    }
    mock_client.async_chat_stream, _ = mock_stream([progress, *text_chunks("Done")])
    result = await _converse(hass, "Hi")
    assert result.response.speech["plain"]["speech"] == "Done"


@pytest.mark.parametrize("conversation_options", [{CONF_LLM_HASS_API: ["missing_api"]}])
async def test_unknown_llm_api(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test an LLM API that no longer exists."""
    result = await _converse(hass, "Hi")
    assert result.response.response_type is intent.IntentResponseType.ERROR
