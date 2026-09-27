"""Tests for the GigaChat config flow."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import (
    CONF_LLM_HASS_API,
    CONF_NAME,
    CONF_PROMPT,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gigachat.api import (
    GigaChatAuthError,
    GigaChatConnectionError,
    GigaChatError,
    GigaChatModel,
    GigaChatScopeError,
    GigaChatSSLError,
)
from custom_components.gigachat.const import (
    CONF_ADVANCED,
    CONF_AUTH_URL,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_CREDENTIALS,
    CONF_MAX_TOKENS,
    CONF_RECOMMENDED,
    CONF_SCOPE,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_AUTH_URL,
    DEFAULT_BASE_URL,
    DEFAULT_PROMPT,
    DOMAIN,
    SCOPE_B2B,
    SCOPE_PERS,
    SUBENTRY_AI_TASK,
    SUBENTRY_CONVERSATION,
)

from .common import AUTH_KEY, CLIENT_ID, CLIENT_SECRET
from .conftest import MODELS

ADVANCED = {
    CONF_VERIFY_SSL: True,
    CONF_BASE_URL: DEFAULT_BASE_URL,
    CONF_AUTH_URL: DEFAULT_AUTH_URL,
}


@pytest.fixture(autouse=True)
async def setup_homeassistant(hass: HomeAssistant) -> None:
    """Set up the core integration, needed by the conversation dependency."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def mock_validate_client() -> Generator[AsyncMock]:
    """Mock the client created by the config flow."""
    client = AsyncMock()
    client.async_get_models.return_value = MODELS
    with patch(
        "custom_components.gigachat.config_flow.async_create_client",
        return_value=client,
    ) as create:
        create.client = client
        yield create


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Do not set up the entry after the flow."""
    with patch(
        "custom_components.gigachat.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry


async def test_user_flow(
    hass: HomeAssistant, mock_validate_client: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test the full user flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CREDENTIALS: f" {AUTH_KEY} ",
            CONF_SCOPE: "pers",
            CONF_ADVANCED: ADVANCED,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "GigaChat"
    assert result["result"].unique_id == CLIENT_ID
    assert result["data"] == {
        CONF_CREDENTIALS: AUTH_KEY,
        CONF_SCOPE: SCOPE_PERS,
        **ADVANCED,
    }
    subentries = {s["subentry_type"]: s for s in result["subentries"]}
    assert subentries[SUBENTRY_CONVERSATION]["data"] == {
        CONF_RECOMMENDED: True,
        CONF_CHAT_MODEL: "GigaChat-3-Ultra",
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_LLM_HASS_API: ["assist"],
    }
    assert subentries[SUBENTRY_AI_TASK]["data"] == {
        CONF_RECOMMENDED: True,
        CONF_CHAT_MODEL: "GigaChat-3-Ultra",
    }
    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_flow_business_account(
    hass: HomeAssistant, mock_validate_client: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test client ID and secret input and the default model of business accounts."""
    mock_validate_client.client.async_get_models.return_value = [
        GigaChatModel("GigaChat-2", "chat"),
        GigaChatModel("GigaChat-2-Max", "chat"),
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CREDENTIALS: f"{CLIENT_ID}:{CLIENT_SECRET}",
            CONF_SCOPE: "b2b",
            CONF_ADVANCED: ADVANCED,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CREDENTIALS] == AUTH_KEY
    assert result["data"][CONF_SCOPE] == SCOPE_B2B
    assert {s["data"][CONF_CHAT_MODEL] for s in result["subentries"]} == {
        "GigaChat-2-Max"
    }


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (GigaChatScopeError("scope"), "invalid_scope"),
        (GigaChatAuthError("auth"), "invalid_auth"),
        (GigaChatSSLError("ssl"), "ssl_error"),
        (GigaChatConnectionError("connection"), "cannot_connect"),
        (GigaChatError("other"), "unknown"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_validate_client: AsyncMock,
    mock_setup_entry: AsyncMock,
    side_effect: Exception,
    error: str,
) -> None:
    """Test errors while validating the credentials, and recovering from them."""
    mock_validate_client.client.async_get_models.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    user_input = {
        CONF_CREDENTIALS: AUTH_KEY,
        CONF_SCOPE: "pers",
        CONF_ADVANCED: ADVANCED,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_validate_client.client.async_get_models.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_credentials_format(
    hass: HomeAssistant, mock_validate_client: AsyncMock
) -> None:
    """Test a string that is not an authorization key."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CREDENTIALS: "hello", CONF_SCOPE: "pers", CONF_ADVANCED: ADVANCED},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CREDENTIALS: "invalid_credentials_format"}
    mock_validate_client.assert_not_called()


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_client: AsyncMock,
) -> None:
    """Test that a project can only be added once."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CREDENTIALS: AUTH_KEY, CONF_SCOPE: "pers", CONF_ADVANCED: ADVANCED},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test entering a new key after the old one was rejected."""
    new_key = AUTH_KEY  # same client, regenerated secret keeps the client ID
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_validate_client.client.async_get_models.side_effect = GigaChatAuthError("x")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CREDENTIALS: new_key}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CREDENTIALS: "garbage"}
    )
    assert result["errors"] == {CONF_CREDENTIALS: "invalid_credentials_format"}

    mock_validate_client.client.async_get_models.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CREDENTIALS: f"{CLIENT_ID}:new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_CREDENTIALS] != AUTH_KEY


async def test_reauth_wrong_account(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_client: AsyncMock,
) -> None:
    """Test that a key of another project is refused."""
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CREDENTIALS: "other-client:secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test changing the scope while keeping the key."""
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SCOPE: "b2b",
            CONF_ADVANCED: {**ADVANCED, CONF_VERIFY_SSL: False},
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_CREDENTIALS] == AUTH_KEY
    assert mock_config_entry.data[CONF_SCOPE] == SCOPE_B2B
    assert mock_config_entry.data[CONF_VERIFY_SSL] is False


async def test_reconfigure_flow_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_client: AsyncMock,
) -> None:
    """Test reconfigure validation."""
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CREDENTIALS: "garbage", CONF_SCOPE: "pers", CONF_ADVANCED: ADVANCED},
    )
    assert result["errors"] == {CONF_CREDENTIALS: "invalid_credentials_format"}

    mock_validate_client.client.async_get_models.side_effect = GigaChatScopeError("x")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SCOPE: "corp", CONF_ADVANCED: ADVANCED}
    )
    assert result["errors"] == {"base": "invalid_scope"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CREDENTIALS: "other:secret", CONF_SCOPE: "pers", CONF_ADVANCED: ADVANCED},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def _start_subentry_flow(
    hass: HomeAssistant, entry: MockConfigEntry, subentry_type: str
) -> Any:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type),
        context={"source": config_entries.SOURCE_USER},
    )


async def test_subentry_conversation_recommended(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test adding a conversation agent with recommended settings."""
    result = await _start_subentry_flow(hass, init_integration, SUBENTRY_CONVERSATION)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    models = result["data_schema"].schema[CONF_CHAT_MODEL].config["options"]
    assert [option["value"] for option in models] == [
        "GigaChat-3-Ultra",
        "GigaChat-2-Max",
        "GigaChat-2-Pro",
        "GigaChat-2",
    ]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Lite agent",
            CONF_CHAT_MODEL: "GigaChat-2",
            CONF_PROMPT: "Be brief",
            CONF_LLM_HASS_API: [],
            CONF_RECOMMENDED: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lite agent"
    assert result["data"] == {
        CONF_RECOMMENDED: True,
        CONF_CHAT_MODEL: "GigaChat-2",
        CONF_PROMPT: "Be brief",
    }


async def test_subentry_ai_task_advanced(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test adding an AI task with custom model parameters."""
    result = await _start_subentry_flow(hass, init_integration, SUBENTRY_AI_TASK)
    assert CONF_PROMPT not in result["data_schema"].schema
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Writer",
            CONF_CHAT_MODEL: "GigaChat-2-Max",
            CONF_RECOMMENDED: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_TEMPERATURE: 0.3, CONF_MAX_TOKENS: 500.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_RECOMMENDED: False,
        CONF_CHAT_MODEL: "GigaChat-2-Max",
        CONF_TEMPERATURE: 0.3,
        CONF_MAX_TOKENS: 500,
    }


async def test_subentry_reconfigure(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test reconfiguring a conversation agent back to recommended settings."""
    subentry = next(
        s
        for s in init_integration.subentries.values()
        if s.subentry_type == SUBENTRY_CONVERSATION
    )
    hass.config_entries.async_update_subentry(
        init_integration,
        subentry,
        data={**subentry.data, CONF_RECOMMENDED: False, CONF_TOP_P: 0.5},
    )
    await hass.async_block_till_done()

    result = await init_integration.start_subentry_reconfigure_flow(
        hass, subentry.subentry_id
    )
    assert result["step_id"] == "init"
    assert CONF_NAME not in result["data_schema"].schema

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CHAT_MODEL: "custom-model",
            CONF_PROMPT: "",
            CONF_LLM_HASS_API: ["assist"],
            CONF_RECOMMENDED: True,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert init_integration.subentries[subentry.subentry_id].data == {
        CONF_RECOMMENDED: True,
        CONF_CHAT_MODEL: "custom-model",
        CONF_LLM_HASS_API: ["assist"],
    }

    # The custom model is offered on the next reconfiguration
    await hass.async_block_till_done()  # the entry reloads after the change
    result = await init_integration.start_subentry_reconfigure_flow(
        hass, subentry.subentry_id
    )
    models = result["data_schema"].schema[CONF_CHAT_MODEL].config["options"]
    assert models[-1]["value"] == "custom-model"


async def test_subentry_entry_not_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that subentries cannot be added while the entry is not loaded."""
    result = await _start_subentry_flow(hass, mock_config_entry, SUBENTRY_CONVERSATION)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"
