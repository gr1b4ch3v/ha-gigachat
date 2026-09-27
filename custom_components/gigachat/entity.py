"""Base entity for the GigaChat integration."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import aclosing, suppress
from dataclasses import dataclass
import mimetypes
from pathlib import Path
import re
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm, translation
from homeassistant.helpers.json import json_dumps
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.ulid import ulid_now
import voluptuous as vol

from .api import (
    IMAGE_STREAM_READ_TIMEOUT,
    STREAM_READ_TIMEOUT,
    GigaChatAuthError,
    GigaChatConnectionError,
    GigaChatError,
    GigaChatPaymentRequiredError,
    GigaChatRateLimitError,
    GigaChatRequestError,
)
from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_RECOMMENDED,
    CONF_REPETITION_PENALTY,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DOMAIN,
    FALLBACK_MODEL,
    IMAGE_MODEL_PREFIX,
    IMAGE_MODELS,
    LOGGER,
    MAX_TOOL_ITERATIONS,
)
from .coordinator import GigaChatConfigEntry, GigaChatCoordinator
from .tools import (
    format_structure,
    format_tool,
    parse_tool_args,
    sanitize_function_name,
)

IMAGE_TAG_RE = re.compile(r"<img\b[^>]*?\bsrc=\"([^\"]+)\"[^>]*>", re.IGNORECASE)
TEXT2IMAGE = "text2image"


@dataclass(slots=True)
class GigaChatNativeContent:
    """Provider specific data stored with an assistant message in the chat log."""

    functions_state_id: str | None = None


async def async_localized_error(
    hass: HomeAssistant, err: HomeAssistantError, language: str | None
) -> str:
    """Return the message of one of our errors in the given language."""
    if err.translation_domain != DOMAIN or not err.translation_key:
        return str(err)
    translations = await translation.async_get_translations(
        hass, language or hass.config.language, "exceptions", [DOMAIN]
    )
    template = translations.get(
        f"component.{DOMAIN}.exceptions.{err.translation_key}.message"
    )
    if template:
        with suppress(KeyError, IndexError, ValueError):
            return template.format(**(err.translation_placeholders or {}))
    return str(err)


class GigaChatBaseLLMEntity(CoordinatorEntity[GigaChatCoordinator]):
    """Base class for the entities backed by a GigaChat model."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: GigaChatConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the entity."""
        super().__init__(entry.runtime_data)
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Sber",
            model=self.model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        # Files uploaded for chat log attachments: (path, size, mtime) -> file ID
        self._uploaded_files: dict[tuple[str, int, float], str] = {}

    @property
    def model(self) -> str:
        """Return the model used by this entity."""
        return self.subentry.data.get(CONF_CHAT_MODEL) or FALLBACK_MODEL

    @property
    def image_model(self) -> str:
        """Return the model used to generate images.

        The GigaChat-3 models refuse to call text2image, so image requests go
        to the best available GigaChat-2 model unless one is configured.
        """
        if self.model.startswith(IMAGE_MODEL_PREFIX):
            return self.model
        available = set(self.coordinator.chat_models)
        return next((m for m in IMAGE_MODELS if m in available), IMAGE_MODELS[0])

    @property
    def _session_id(self) -> str:
        """Return a stable session ID so GigaChat can cache the shared prompt."""
        return f"ha-{self.subentry.subentry_id}"

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure: vol.Schema | None = None,
        *,
        generate_image: bool = False,
    ) -> None:
        """Generate the answer for the chat log, running tool calls as needed."""
        options = self.subentry.data
        payload: dict[str, Any] = {
            "model": self.image_model if generate_image else self.model
        }
        if not options.get(CONF_RECOMMENDED, True):
            for key in (
                CONF_TEMPERATURE,
                CONF_TOP_P,
                CONF_MAX_TOKENS,
                CONF_REPETITION_PENALTY,
            ):
                if (value := options.get(key)) is not None:
                    payload[key] = value

        # Function name as sent to GigaChat -> (tool name, parameter schema)
        functions: dict[str, tuple[str, dict[str, Any]]] = {}
        if generate_image:
            # In automatic mode the model sometimes describes the picture
            # instead of drawing it, forcing the built-in function is reliable.
            payload["functions"] = [{"name": TEXT2IMAGE}]
            payload["function_call"] = {"name": TEXT2IMAGE}
        elif chat_log.llm_api and chat_log.llm_api.tools:
            specs = []
            for tool in chat_log.llm_api.tools:
                spec = format_tool(tool, chat_log.llm_api.custom_serializer)
                functions[spec["name"]] = (tool.name, spec["parameters"])
                specs.append(spec)
            payload["functions"] = specs
            payload["function_call"] = "auto"

        if structure is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "schema": format_structure(
                    structure,
                    chat_log.llm_api.custom_serializer
                    if chat_log.llm_api
                    else llm.selector_serializer,
                ),
                "strict": True,
            }

        tool_to_function = {tool: name for name, (tool, _) in functions.items()}
        client = self.coordinator.client

        # To prevent infinite loops, we limit the number of iterations
        for _iteration in range(MAX_TOOL_ITERATIONS):
            payload["messages"] = await self._async_convert_chat_log(
                chat_log, tool_to_function
            )
            try:
                async with aclosing(
                    client.async_chat_stream(
                        payload,
                        # One-shot image prompts gain nothing from the cache.
                        session_id=None if generate_image else self._session_id,
                        read_timeout=IMAGE_STREAM_READ_TIMEOUT
                        if generate_image
                        else STREAM_READ_TIMEOUT,
                    )
                ) as stream:
                    async for _content in chat_log.async_add_delta_content_stream(
                        self.entity_id,
                        self._async_transform_stream(chat_log, stream, functions),
                    ):
                        pass
            except GigaChatError as err:
                raise self._convert_error(err) from err

            if not chat_log.unresponded_tool_results:
                break

    async def _async_transform_stream(
        self,
        chat_log: conversation.ChatLog,
        stream: AsyncIterator[dict[str, Any]],
        functions: Mapping[str, tuple[str, dict[str, Any]]],
    ) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
        """Transform GigaChat stream chunks into chat log deltas."""
        started = False
        functions_state_id: str | None = None
        finish_reason: str | None = None

        async for chunk in stream:
            if usage := chunk.get("usage"):
                self._async_record_usage(chat_log, usage)
            for choice in chunk.get("choices") or ():
                delta = choice.get("delta") or choice.get("message") or {}
                if delta.get("role") == "function_in_progress":
                    # Progress of a built-in function such as text2image.
                    LOGGER.debug("%s: %s", delta.get("name"), delta.get("content"))
                    continue
                if not started:
                    started = True
                    yield {"role": "assistant"}
                if content := delta.get("content"):
                    yield {"content": content}
                if (function_call := delta.get("function_call")) and (
                    name := function_call.get("name")
                ):
                    tool_name, parameters = functions.get(name, (name, {}))
                    yield {
                        "tool_calls": [
                            llm.ToolInput(
                                id=str(function_call.get("id") or ulid_now()),
                                tool_name=tool_name,
                                tool_args=parse_tool_args(
                                    function_call.get("arguments"), parameters
                                ),
                            )
                        ]
                    }
                if state_id := delta.get("functions_state_id"):
                    functions_state_id = state_id
                if reason := choice.get("finish_reason"):
                    finish_reason = reason

        if not started:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="empty_response"
            )
        if finish_reason == "error":
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_function_call"
            )
        if finish_reason == "blacklist":
            LOGGER.debug("The request was declined by the GigaChat content filter")
        if functions_state_id:
            yield {"native": GigaChatNativeContent(functions_state_id)}

    def _async_record_usage(
        self, chat_log: conversation.ChatLog, usage: Mapping[str, Any]
    ) -> None:
        """Add token usage to the conversation trace and the usage sensors."""
        chat_log.async_trace(
            {
                "stats": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "cached_input_tokens": usage.get("precached_prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                }
            }
        )
        self.coordinator.async_record_usage(usage)

    async def _async_convert_chat_log(
        self, chat_log: conversation.ChatLog, tool_to_function: Mapping[str, str]
    ) -> list[dict[str, Any]]:
        """Convert the chat log into GigaChat messages.

        GigaChat accepts one system message, first, and expects every function
        call to be directly followed by its result. It returns a single call per
        message, so calls are replayed one by one with their results.
        """
        results = {
            content.tool_call_id: content
            for content in chat_log.content
            if isinstance(content, conversation.ToolResultContent)
        }
        messages: list[dict[str, Any]] = []
        for content in chat_log.content:
            if isinstance(content, conversation.SystemContent):
                if content.content:
                    role = "user" if messages else "system"
                    messages.append({"role": role, "content": content.content})
            elif isinstance(content, conversation.UserContent):
                messages.extend(await self._async_convert_user_content(content))
            elif isinstance(content, conversation.AssistantContent):
                messages.extend(
                    _convert_assistant_content(content, results, tool_to_function)
                )
            # Tool results are added right after the corresponding call.
        return messages

    async def _async_convert_user_content(
        self, content: conversation.UserContent
    ) -> list[dict[str, Any]]:
        """Convert a user message, uploading its attachments to the storage."""
        if not content.attachments:
            return [{"role": "user", "content": content.content}]
        file_ids = [
            await self._async_upload_attachment(attachment)
            for attachment in content.attachments
        ]
        # A message may carry only one image, so extra attachments get their own.
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"[{attachment.path.name}]",
                "attachments": [file_id],
            }
            for attachment, file_id in zip(
                content.attachments[:-1], file_ids[:-1], strict=True
            )
        ]
        messages.append(
            {"role": "user", "content": content.content, "attachments": [file_ids[-1]]}
        )
        return messages

    async def _async_upload_attachment(
        self, attachment: conversation.Attachment
    ) -> str:
        """Upload an attachment once and return its file ID."""
        path: Path = attachment.path
        try:
            stat = await self.hass.async_add_executor_job(path.stat)
        except OSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="attachment_not_found",
                translation_placeholders={"path": str(path)},
            ) from err
        key = (str(path), stat.st_size, stat.st_mtime)
        if file_id := self._uploaded_files.get(key):
            return file_id
        data = await self.hass.async_add_executor_job(path.read_bytes)
        mime_type = (
            attachment.mime_type
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        try:
            file_id = await self.coordinator.client.async_upload_file(
                data, path.name, mime_type
            )
        except GigaChatError as err:
            raise self._convert_error(err) from err
        self._uploaded_files[key] = file_id
        return file_id

    async def _async_delete_uploads(self) -> None:
        """Remove the uploaded attachments from the GigaChat storage."""
        client = self.coordinator.client
        while self._uploaded_files:
            _, file_id = self._uploaded_files.popitem()
            try:
                await client.async_delete_file(file_id)
            except GigaChatError as err:
                LOGGER.debug("Could not delete file %s: %s", file_id, err)

    def _convert_error(self, err: GigaChatError) -> HomeAssistantError:
        """Convert an API error into a user facing error."""
        if isinstance(err, GigaChatAuthError):
            self.entry.async_start_reauth(self.hass)
            key = "auth_failed"
        elif isinstance(err, GigaChatPaymentRequiredError):
            key = "payment_required"
        elif isinstance(err, GigaChatRateLimitError):
            key = "rate_limited"
        elif isinstance(err, GigaChatConnectionError):
            key = "connection_error"
        elif isinstance(err, GigaChatRequestError) and "image" in err.message.lower():
            key = "image_not_supported"
        elif isinstance(err, GigaChatRequestError):
            key = "request_error"
        else:
            key = "api_error"
        LOGGER.debug("GigaChat request failed: %r", err)
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"message": err.message, "model": self.model},
        )


def _convert_assistant_content(
    content: conversation.AssistantContent,
    results: Mapping[str, conversation.ToolResultContent],
    tool_to_function: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Convert an assistant message and the results of its tool calls."""
    text = IMAGE_TAG_RE.sub("", content.content or "").strip()
    calls = [call for call in content.tool_calls or () if call.id in results]
    if not calls:
        return [{"role": "assistant", "content": text}] if text else []

    state_id = (
        content.native.functions_state_id
        if isinstance(content.native, GigaChatNativeContent)
        else None
    )
    messages: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        name = tool_to_function.get(call.tool_name) or sanitize_function_name(
            call.tool_name
        )
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text if index == 0 else "",
            "function_call": {"name": name, "arguments": call.tool_args},
        }
        if state_id:
            message["functions_state_id"] = state_id
        messages.append(message)
        result = results[call.id].tool_result
        messages.append(
            {
                "role": "function",
                "name": name,
                # Must be a JSON object, plain values are rejected with HTTP 422.
                "content": json_dumps(
                    result if isinstance(result, Mapping) else {"result": result}
                ),
            }
        )
    return messages
