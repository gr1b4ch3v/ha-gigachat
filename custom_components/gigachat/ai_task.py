"""AI Task entity backed by GigaChat."""

from __future__ import annotations

from json import JSONDecodeError
import struct
from typing import override

from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .api import GigaChatError
from .const import DOMAIN, LOGGER, SUBENTRY_AI_TASK
from .coordinator import GigaChatConfigEntry
from .entity import IMAGE_TAG_RE, GigaChatBaseLLMEntity

# Requests are serialized by the API client, which knows the account limits.
PARALLEL_UPDATES = 0

IMAGE_ATTEMPTS = 3


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: GigaChatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the AI Task entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_AI_TASK:
            continue
        async_add_entities(
            [GigaChatTaskEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class GigaChatTaskEntity(ai_task.AITaskEntity, GigaChatBaseLLMEntity):
    """GigaChat AI Task entity."""

    _attr_translation_key = "ai_task_data"
    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        | ai_task.AITaskEntityFeature.GENERATE_IMAGE
    )

    @override
    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        try:
            await self._async_handle_chat_log(chat_log, task.structure)
        finally:
            await self._async_delete_uploads()

        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="empty_response"
            )
        text = chat_log.content[-1].content or ""

        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id, data=text
            )
        try:
            data = json_loads(_strip_code_fence(text))
        except JSONDecodeError as err:
            LOGGER.error("Failed to parse the structured response: %s", text)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_structured_response"
            ) from err
        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id, data=data
        )

    @override
    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Handle a generate image task with the built-in text2image function."""
        start = len(chat_log.content)
        file_ids: list[str] = []
        response = ""
        try:
            for attempt in range(1, IMAGE_ATTEMPTS + 1):
                await self._async_handle_chat_log(chat_log, generate_image=True)
                answers = [
                    content.content
                    for content in chat_log.content[start:]
                    if isinstance(content, conversation.AssistantContent)
                    and content.content
                ]
                file_ids = [
                    file_id
                    for text in answers
                    for file_id in IMAGE_TAG_RE.findall(text)
                ]
                if file_ids:
                    break
                # text2image fails intermittently and the model apologizes instead.
                response = " ".join(IMAGE_TAG_RE.sub("", text) for text in answers)
                LOGGER.debug("No image on attempt %s: %s", attempt, response)
                # Retry from a clean state, the apology would discourage the model.
                del chat_log.content[start:]
        finally:
            await self._async_delete_uploads()

        if not file_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_image_generated",
                translation_placeholders={"message": response.strip()[:300]},
            )

        client = self.coordinator.client
        try:
            image_data, mime_type = await client.async_download_file(file_ids[0])
        except GigaChatError as err:
            raise self._convert_error(err) from err
        finally:
            # The image is stored by Home Assistant, keep the GigaChat storage clean.
            for file_id in file_ids:
                try:
                    await client.async_delete_file(file_id)
                except GigaChatError as err:
                    LOGGER.debug("Could not delete image %s: %s", file_id, err)

        width, height = _image_size(image_data)
        return ai_task.GenImageTaskResult(
            image_data=image_data,
            conversation_id=chat_log.conversation_id,
            mime_type=(
                mime_type
                if mime_type and mime_type.startswith("image/")
                else "image/jpeg"
            ),
            width=width,
            height=height,
            model=self.image_model,
        )


def _strip_code_fence(text: str) -> str:
    """Remove a Markdown code fence around a JSON answer, if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _image_size(data: bytes) -> tuple[int | None, int | None]:
    """Return the dimensions of a JPEG or PNG image."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            (length,) = struct.unpack(">H", data[index + 2 : index + 4])
            # SOF markers carry the frame size (C4, C8 and CC are not SOF).
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                return width, height
            index += 2 + length
    return None, None
