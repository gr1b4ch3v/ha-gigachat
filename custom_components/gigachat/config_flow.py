"""Config flow for the GigaChat integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    CONF_LLM_HASS_API,
    CONF_NAME,
    CONF_PROMPT,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.typing import VolDictType
import voluptuous as vol

from .api import (
    GigaChatAuthError,
    GigaChatConnectionError,
    GigaChatError,
    GigaChatModel,
    GigaChatScopeError,
    GigaChatSSLError,
    client_id_from_credentials,
    normalize_credentials,
)
from .const import (
    CONF_ADVANCED,
    CONF_AUTH_URL,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_CREDENTIALS,
    CONF_MAX_TOKENS,
    CONF_RECOMMENDED,
    CONF_REPETITION_PENALTY,
    CONF_SCOPE,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AUTH_URL,
    DEFAULT_BASE_URL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_PROMPT,
    DEFAULT_TITLE,
    DOMAIN,
    LOGGER,
    SCOPE_OPTIONS,
    STUDIO_URL,
    SUBENTRY_AI_TASK,
    SUBENTRY_CONVERSATION,
)
from .coordinator import GigaChatConfigEntry, async_create_client, pick_default_model

DOCS_URL = "https://developers.sber.ru/docs/ru/gigachat/quickstart/ind-using-api"
# URLs must not be part of translations, they are passed as placeholders.
DEFAULT_URL_PLACEHOLDERS = {
    "default_base_url": DEFAULT_BASE_URL,
    "default_auth_url": DEFAULT_AUTH_URL,
}

ADVANCED_MODEL_OPTIONS = (
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_MAX_TOKENS,
    CONF_REPETITION_PENALTY,
)

SCOPE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(SCOPE_OPTIONS),
        translation_key=CONF_SCOPE,
        mode=SelectSelectorMode.LIST,
    )
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def _advanced_section(defaults: Mapping[str, Any]) -> section:
    """Return the collapsed section with connection settings."""
    return section(
        vol.Schema(
            {
                vol.Required(
                    CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
                ): BooleanSelector(),
                vol.Required(
                    CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                ): URL_SELECTOR,
                vol.Required(
                    CONF_AUTH_URL, default=defaults.get(CONF_AUTH_URL, DEFAULT_AUTH_URL)
                ): URL_SELECTOR,
            }
        ),
        {"collapsed": True},
    )


def _scope_option(scope: str | None) -> str:
    """Return the selector option of an API scope."""
    return next(
        (option for option, value in SCOPE_OPTIONS.items() if value == scope), "pers"
    )


def _flatten(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Merge the advanced section into the top level data."""
    data = {k: v for k, v in user_input.items() if k != CONF_ADVANCED}
    data.update(user_input.get(CONF_ADVANCED, {}))
    if CONF_SCOPE in data:
        data[CONF_SCOPE] = SCOPE_OPTIONS.get(data[CONF_SCOPE], data[CONF_SCOPE])
    for key in (CONF_BASE_URL, CONF_AUTH_URL):
        if isinstance(data.get(key), str):
            data[key] = data[key].strip()
    return data


async def _async_validate(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[dict[str, str], list[GigaChatModel]]:
    """Check that the credentials work. Return errors and the available models."""
    try:
        client = await async_create_client(hass, data)
        models = await client.async_get_models()
    except GigaChatScopeError:
        return {"base": "invalid_scope"}, []
    except GigaChatAuthError:
        return {"base": "invalid_auth"}, []
    except GigaChatSSLError:
        return {"base": "ssl_error"}, []
    except GigaChatConnectionError:
        return {"base": "cannot_connect"}, []
    except GigaChatError as err:
        LOGGER.warning("Unexpected response from GigaChat: %s", err)
        return {"base": "unknown"}, []
    except Exception:
        LOGGER.exception("Unexpected exception while validating GigaChat access")
        return {"base": "unknown"}, []
    return {}, models


def default_subentry_options(subentry_type: str, model: str) -> dict[str, Any]:
    """Return the default options of a new subentry."""
    options: dict[str, Any] = {CONF_RECOMMENDED: True, CONF_CHAT_MODEL: model}
    if subentry_type == SUBENTRY_CONVERSATION:
        options[CONF_PROMPT] = DEFAULT_PROMPT
        options[CONF_LLM_HASS_API] = [llm.LLM_API_ASSIST]
    return options


class GigaChatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GigaChat."""

    VERSION = 1
    MINOR_VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the authorization key and the API scope."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _flatten(user_input)
            try:
                data[CONF_CREDENTIALS] = normalize_credentials(data[CONF_CREDENTIALS])
            except ValueError:
                errors[CONF_CREDENTIALS] = "invalid_credentials_format"
            else:
                await self.async_set_unique_id(
                    client_id_from_credentials(data[CONF_CREDENTIALS])
                )
                self._abort_if_unique_id_configured()
                errors, models = await _async_validate(self.hass, data)
                if not errors:
                    model = pick_default_model(models)
                    return self.async_create_entry(
                        title=DEFAULT_TITLE,
                        data=data,
                        subentries=[
                            {
                                "subentry_type": SUBENTRY_CONVERSATION,
                                "data": default_subentry_options(
                                    SUBENTRY_CONVERSATION, model
                                ),
                                "title": DEFAULT_CONVERSATION_NAME,
                                "unique_id": None,
                            },
                            {
                                "subentry_type": SUBENTRY_AI_TASK,
                                "data": default_subentry_options(
                                    SUBENTRY_AI_TASK, model
                                ),
                                "title": DEFAULT_AI_TASK_NAME,
                                "unique_id": None,
                            },
                        ],
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_CREDENTIALS): PASSWORD_SELECTOR,
                vol.Required(CONF_SCOPE, default="pers"): SCOPE_SELECTOR,
                vol.Required(CONF_ADVANCED): _advanced_section({}),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders={
                "studio_url": STUDIO_URL,
                "docs_url": DOCS_URL,
                **DEFAULT_URL_PLACEHOLDERS,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle rejected credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new authorization key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                credentials = normalize_credentials(user_input[CONF_CREDENTIALS])
            except ValueError:
                errors[CONF_CREDENTIALS] = "invalid_credentials_format"
            else:
                await self.async_set_unique_id(client_id_from_credentials(credentials))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                data = {**entry.data, CONF_CREDENTIALS: credentials}
                errors, _ = await _async_validate(self.hass, data)
                if not errors:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_CREDENTIALS: credentials}
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CREDENTIALS): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={"studio_url": STUDIO_URL},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the credentials, the scope or the connection settings."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            data = {**entry.data, **_flatten(user_input)}
            try:
                if raw_credentials := user_input.get(CONF_CREDENTIALS):
                    data[CONF_CREDENTIALS] = normalize_credentials(raw_credentials)
                else:
                    data[CONF_CREDENTIALS] = entry.data[CONF_CREDENTIALS]
            except ValueError:
                errors[CONF_CREDENTIALS] = "invalid_credentials_format"
            else:
                await self.async_set_unique_id(
                    client_id_from_credentials(data[CONF_CREDENTIALS])
                )
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                errors, _ = await _async_validate(self.hass, data)
                if not errors:
                    return self.async_update_reload_and_abort(entry, data=data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_CREDENTIALS): PASSWORD_SELECTOR,
                vol.Required(
                    CONF_SCOPE, default=_scope_option(entry.data.get(CONF_SCOPE))
                ): SCOPE_SELECTOR,
                vol.Required(CONF_ADVANCED): _advanced_section(entry.data),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "studio_url": STUDIO_URL,
                **DEFAULT_URL_PLACEHOLDERS,
            },
        )

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            SUBENTRY_CONVERSATION: GigaChatSubentryFlowHandler,
            SUBENTRY_AI_TASK: GigaChatSubentryFlowHandler,
        }


class GigaChatSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure a conversation agent or an AI task."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        entry: GigaChatConfigEntry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        model = pick_default_model(entry.runtime_data.data.models)
        self.options = default_subentry_options(self._subentry_type, model)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure a subentry."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the model and the basic settings."""
        entry: GigaChatConfigEntry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)
                self.options.pop(CONF_LLM_HASS_API, None)
            if not user_input.get(CONF_PROMPT):
                user_input.pop(CONF_PROMPT, None)
                self.options.pop(CONF_PROMPT, None)
            self.options.update(user_input)
            if user_input[CONF_RECOMMENDED]:
                for key in ADVANCED_MODEL_OPTIONS:
                    self.options.pop(key, None)
                return self._async_finish()
            return await self.async_step_advanced()

        known_apis = {api.id: api.name for api in llm.async_get_apis(self.hass)}
        if selected := self.options.get(CONF_LLM_HASS_API):
            if isinstance(selected, str):
                selected = [selected]
            self.options[CONF_LLM_HASS_API] = [
                api for api in selected if api in known_apis
            ]

        models = entry.runtime_data.chat_models
        current = self.options.get(CONF_CHAT_MODEL)
        if current and current not in models:
            models = [*models, current]

        schema: VolDictType = {}
        if self._is_new:
            default_name = (
                DEFAULT_AI_TASK_NAME
                if self._subentry_type == SUBENTRY_AI_TASK
                else DEFAULT_CONVERSATION_NAME
            )
            schema[vol.Required(CONF_NAME, default=default_name)] = str
        schema[vol.Required(CONF_CHAT_MODEL)] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=model, label=model) for model in models
                ],
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        )
        if self._subentry_type == SUBENTRY_CONVERSATION:
            schema[vol.Optional(CONF_PROMPT)] = TemplateSelector()
            schema[vol.Optional(CONF_LLM_HASS_API)] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=api_id, label=name)
                        for api_id, name in known_apis.items()
                    ],
                    multiple=True,
                )
            )
        schema[vol.Required(CONF_RECOMMENDED, default=True)] = bool

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Tune the sampling parameters. Empty fields use the model defaults."""
        if user_input is not None:
            for key in ADVANCED_MODEL_OPTIONS:
                if user_input.get(key) is None:
                    self.options.pop(key, None)
                else:
                    self.options[key] = user_input[key]
            if CONF_MAX_TOKENS in self.options:
                self.options[CONF_MAX_TOKENS] = int(self.options[CONF_MAX_TOKENS])
            return self._async_finish()

        schema: VolDictType = {
            vol.Optional(CONF_TEMPERATURE): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=2, step=0.05, mode=NumberSelectorMode.SLIDER
                )
            ),
            vol.Optional(CONF_TOP_P): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=1, step=0.01, mode=NumberSelectorMode.SLIDER
                )
            ),
            vol.Optional(CONF_MAX_TOKENS): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=131072, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(CONF_REPETITION_PENALTY): NumberSelector(
                NumberSelectorConfig(
                    min=0.1, max=2, step=0.05, mode=NumberSelectorMode.SLIDER
                )
            ),
        }
        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
            last_step=True,
        )

    @callback
    def _async_finish(self) -> SubentryFlowResult:
        """Create or update the subentry."""
        if self._is_new:
            return self.async_create_entry(
                title=self.options.pop(CONF_NAME), data=self.options
            )
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), data=self.options
        )
