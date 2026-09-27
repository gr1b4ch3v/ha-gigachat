"""Conversation agent backed by GigaChat."""

from __future__ import annotations

from typing import Literal, override

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_CONVERSATION
from .coordinator import GigaChatConfigEntry
from .entity import GigaChatBaseLLMEntity, async_localized_error

# Requests are serialized by the API client, which knows the account limits.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: GigaChatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation agents."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_CONVERSATION:
            continue
        async_add_entities(
            [GigaChatConversationEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class GigaChatConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
    GigaChatBaseLLMEntity,
):
    """GigaChat conversation agent."""

    _attr_supports_streaming = True
    _attr_translation_key = "conversation"

    def __init__(self, entry: GigaChatConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the agent."""
        super().__init__(entry, subentry)
        if self.subentry.data.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @override
    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process a sentence."""
        options = self.subentry.data
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        try:
            await self._async_handle_chat_log(chat_log)
        except HomeAssistantError as err:
            if err.translation_domain != DOMAIN:
                raise
            # Speak the error in the language of the conversation, the
            # conversation manager would only use the English message.
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                await async_localized_error(self.hass, err, user_input.language),
            )
            return conversation.ConversationResult(
                response=response, conversation_id=chat_log.conversation_id
            )

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
