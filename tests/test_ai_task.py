"""Tests for the GigaChat AI Task entity."""

from __future__ import annotations

from pathlib import Path
import re
import struct
from unittest.mock import AsyncMock, patch

from homeassistant.components import ai_task, conversation, media_source
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.gigachat.ai_task import _image_size, _strip_code_fence
from custom_components.gigachat.api import GigaChatError, GigaChatRequestError
from custom_components.gigachat.const import CONF_CHAT_MODEL, CONF_RECOMMENDED

from .common import mock_stream, text_chunks

ENTITY_ID = "ai_task.gigachat_ai_task"
JPEG_1024 = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 768, 1024) + b"\x03\x01\x22\x00"
)


async def test_generate_text(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test generating text."""
    mock_client.async_chat_stream, payloads = mock_stream(text_chunks("Helios"))
    result = await ai_task.async_generate_data(
        hass, task_name="Name", entity_id=ENTITY_ID, instructions="Name my home"
    )
    assert result.data == "Helios"
    payload = payloads[0]
    assert payload["model"] == "GigaChat-3-Ultra"
    assert "functions" not in payload
    assert "response_format" not in payload
    assert payload["messages"][-1] == {"role": "user", "content": "Name my home"}
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["supported_features"] == 7


async def test_generate_structured(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test generating structured data."""
    mock_client.async_chat_stream, payloads = mock_stream(
        text_chunks('```json\n{"condition": "sunny", ', '"temperature": 23}\n```')
    )
    result = await ai_task.async_generate_data(
        hass,
        task_name="Weather",
        entity_id=ENTITY_ID,
        instructions="Describe the weather",
        structure=vol.Schema(
            {vol.Required("condition"): str, vol.Optional("temperature"): int}
        ),
    )
    assert result.data == {"condition": "sunny", "temperature": 23}
    response_format = payloads[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    assert response_format["schema"]["required"] == ["condition", "temperature"]


async def test_generate_structured_invalid_json(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test a structured answer that is not JSON."""
    mock_client.async_chat_stream, _ = mock_stream(text_chunks("Sunny and warm"))
    with pytest.raises(HomeAssistantError, match="did not return valid JSON"):
        await ai_task.async_generate_data(
            hass,
            task_name="Weather",
            entity_id=ENTITY_ID,
            instructions="Describe the weather",
            structure=vol.Schema({vol.Required("condition"): str}),
        )


async def test_generate_with_attachment(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    tmp_path: Path,
) -> None:
    """Test that attachments are uploaded, referenced and deleted afterwards."""
    first = tmp_path / "front_door.jpg"
    first.write_bytes(b"one")
    second = tmp_path / "garden.png"
    second.write_bytes(b"two")
    mock_client.async_upload_file.side_effect = ["file-a", "file-b"]
    mock_client.async_chat_stream, payloads = mock_stream(text_chunks("A courier"))

    with patch(
        "homeassistant.components.ai_task.task._resolve_attachments",
        return_value=[
            _attachment(first, "image/jpeg"),
            _attachment(second, "image/png"),
        ],
    ):
        result = await ai_task.async_generate_data(
            hass,
            task_name="Camera",
            entity_id=ENTITY_ID,
            instructions="Who is at the door?",
            attachments=[
                {
                    "media_content_id": "media-source://x",
                    "media_content_type": "image/jpeg",
                }
            ],
        )

    assert result.data == "A courier"
    assert payloads[0]["messages"][-2:] == [
        {"role": "user", "content": "[front_door.jpg]", "attachments": ["file-a"]},
        {"role": "user", "content": "Who is at the door?", "attachments": ["file-b"]},
    ]
    uploads = [call.args for call in mock_client.async_upload_file.call_args_list]
    assert uploads == [
        (b"one", "front_door.jpg", "image/jpeg"),
        (b"two", "garden.png", "image/png"),
    ]
    assert sorted(c.args[0] for c in mock_client.async_delete_file.call_args_list) == [
        "file-a",
        "file-b",
    ]


async def test_attachment_upload_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    tmp_path: Path,
) -> None:
    """Test an attachment that GigaChat refuses and a missing attachment."""
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"data")
    mock_client.async_upload_file.side_effect = GigaChatRequestError("bad file", 422)
    for attachments, match in (
        ([_attachment(image, "image/jpeg")], "rejected the request: bad file"),
        ([_attachment(tmp_path / "missing.jpg", "image/jpeg")], "was not found"),
    ):
        with (
            patch(
                "homeassistant.components.ai_task.task._resolve_attachments",
                return_value=attachments,
            ),
            pytest.raises(HomeAssistantError, match=match),
        ):
            await ai_task.async_generate_data(
                hass,
                task_name="Camera",
                entity_id=ENTITY_ID,
                instructions="Describe",
                attachments=[
                    {"media_content_id": "x", "media_content_type": "image/jpeg"}
                ],
            )


@pytest.fixture
async def media(hass: HomeAssistant) -> None:
    """Set up media source for storing generated images."""
    assert await async_setup_component(hass, media_source.DOMAIN, {})


async def test_generate_image(
    hass: HomeAssistant,
    media: None,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Test generating an image, with a retry after a failed attempt."""
    mock_client.async_download_file.return_value = (JPEG_1024, "image/jpeg")
    mock_client.async_chat_stream, payloads = mock_stream(
        text_chunks("There was an issue generating the image."),
        text_chunks('<img src="img-1" fuse="true"/>', " Here is your kitchen."),
    )

    with (
        patch(
            "homeassistant.components.media_source.local_source.LocalSource.async_upload_media",
            return_value="media-source://ai_task/image/kitchen.jpg",
        ),
        patch(
            "homeassistant.components.ai_task.task.async_sign_path",
            return_value="/signed",
        ),
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            return_value=media_source.PlayMedia(
                url="/media/kitchen.jpg", mime_type="image/jpeg"
            ),
        ),
        patch(
            "homeassistant.components.media_source.local_source.LocalSource.async_resolve_media",
            return_value=media_source.PlayMedia(
                url="/media/kitchen.jpg", mime_type="image/jpeg"
            ),
        ),
    ):
        result = await ai_task.async_generate_image(
            hass,
            task_name="Kitchen",
            entity_id=ENTITY_ID,
            instructions="A cozy kitchen",
        )

    assert result["mime_type"] == "image/jpeg"
    assert result["width"] == 1024
    assert result["height"] == 768
    # GigaChat-3 models cannot draw, the request goes to a GigaChat-2 model
    assert result["model"] == "GigaChat-2-Max"
    assert len(payloads) == 2
    for payload in payloads:
        assert payload["model"] == "GigaChat-2-Max"
        assert payload["function_call"] == {"name": "text2image"}
        assert payload["_session_id"] is None
        assert payload["_read_timeout"] == 120
        # The failed attempt is not part of the retried conversation
        assert payload["messages"][-1] == {"role": "user", "content": "A cozy kitchen"}
    mock_client.async_download_file.assert_called_once_with("img-1")
    mock_client.async_delete_file.assert_called_once_with("img-1")


async def test_generate_image_fails(
    hass: HomeAssistant,
    media: None,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Test that the model response is reported when no image is generated."""
    mock_client.async_chat_stream, payloads = mock_stream(
        *(text_chunks("Sorry, I cannot draw that.") for _ in range(3))
    )
    with pytest.raises(
        HomeAssistantError, match=re.escape("Sorry, I cannot draw that.")
    ):
        await ai_task.async_generate_image(
            hass, task_name="Kitchen", entity_id=ENTITY_ID, instructions="Draw"
        )
    assert len(payloads) == 3


@pytest.mark.parametrize(
    "ai_task_options", [{CONF_RECOMMENDED: True, CONF_CHAT_MODEL: "GigaChat-2-Pro"}]
)
async def test_generate_image_download_error(
    hass: HomeAssistant,
    media: None,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Test a failed download and that a configured GigaChat-2 model is used."""
    mock_client.async_chat_stream, payloads = mock_stream(
        text_chunks('<img src="img-2"/>')
    )
    mock_client.async_download_file.side_effect = GigaChatError("gone")
    mock_client.async_delete_file.side_effect = GigaChatError("gone")
    with pytest.raises(HomeAssistantError, match="gone"):
        await ai_task.async_generate_image(
            hass, task_name="Kitchen", entity_id=ENTITY_ID, instructions="Draw"
        )
    assert payloads[0]["model"] == "GigaChat-2-Pro"
    mock_client.async_delete_file.assert_called_once_with("img-2")


def test_image_size() -> None:
    """Test reading image dimensions."""
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480)
    assert _image_size(png) == (640, 480)
    assert _image_size(JPEG_1024) == (1024, 768)
    assert _image_size(b"\xff\xd8\xff") == (None, None)
    assert _image_size(b"GIF89a") == (None, None)


def test_strip_code_fence() -> None:
    """Test removing Markdown fences around JSON."""
    assert _strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'
    assert _strip_code_fence("```") == ""


def _attachment(path: Path, mime_type: str) -> conversation.Attachment:
    return conversation.Attachment(
        media_content_id=f"media-source://media_source/local/{path.name}",
        mime_type=mime_type,
        path=path,
    )
