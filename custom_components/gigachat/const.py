"""Constants for the GigaChat integration."""

from datetime import timedelta
import logging
from typing import Final

from homeassistant.helpers import llm

DOMAIN: Final = "gigachat"
LOGGER = logging.getLogger(__package__)

# Config entry data
CONF_CREDENTIALS: Final = "credentials"
CONF_SCOPE: Final = "scope"
CONF_BASE_URL: Final = "base_url"
CONF_AUTH_URL: Final = "auth_url"
CONF_ADVANCED: Final = "advanced"

# Subentry options
CONF_CHAT_MODEL: Final = "chat_model"
CONF_RECOMMENDED: Final = "recommended"
CONF_TEMPERATURE: Final = "temperature"
CONF_TOP_P: Final = "top_p"
CONF_MAX_TOKENS: Final = "max_tokens"
CONF_REPETITION_PENALTY: Final = "repetition_penalty"

SUBENTRY_CONVERSATION: Final = "conversation"
SUBENTRY_AI_TASK: Final = "ai_task_data"

# API scopes: individuals, business with prepaid packages, business pay-as-you-go
SCOPE_PERS: Final = "GIGACHAT_API_PERS"
SCOPE_B2B: Final = "GIGACHAT_API_B2B"
SCOPE_CORP: Final = "GIGACHAT_API_CORP"
SCOPES: Final = (SCOPE_PERS, SCOPE_B2B, SCOPE_CORP)
# Selector option -> scope (translation keys must be lowercase)
SCOPE_OPTIONS: Final = {"pers": SCOPE_PERS, "b2b": SCOPE_B2B, "corp": SCOPE_CORP}

DEFAULT_BASE_URL: Final = "https://api.giga.chat/v1"
DEFAULT_AUTH_URL: Final = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

# Individuals get a single concurrent stream; business accounts get ten.
MAX_CONCURRENT_REQUESTS: Final = {SCOPE_PERS: 1, SCOPE_B2B: 10, SCOPE_CORP: 10}

# Preferred default models, in order. GigaChat-3-Ultra is only offered to
# individuals (freemium), so business accounts fall through to GigaChat-2-Max.
PREFERRED_MODELS: Final = (
    "GigaChat-3-Ultra",
    "GigaChat-2-Max",
    "GigaChat-2-Pro",
    "GigaChat-2",
)
FALLBACK_MODEL: Final = "GigaChat-2-Max"
# Only the GigaChat-2 family can call the built-in text2image function.
IMAGE_MODEL_PREFIX: Final = "GigaChat-2"
IMAGE_MODELS: Final = ("GigaChat-2-Max", "GigaChat-2-Pro", "GigaChat-2")

DEFAULT_TITLE: Final = "GigaChat"
DEFAULT_CONVERSATION_NAME: Final = "GigaChat"
DEFAULT_AI_TASK_NAME: Final = "GigaChat AI Task"

# GigaChat tends to answer with Markdown or LaTeX, which breaks text-to-speech.
DEFAULT_PROMPT: Final = (
    llm.DEFAULT_INSTRUCTIONS_PROMPT
    + "Reply in the language of the user's request. Never use Markdown, LaTeX,"
    " emoji or other markup: your answers may be read aloud.\n"
)

# Max number of back and forth with the LLM to generate a response
MAX_TOOL_ITERATIONS: Final = 10

UPDATE_INTERVAL: Final = timedelta(minutes=30)
UPDATE_INTERVAL_ERROR: Final = timedelta(minutes=2)
MODELS_REFRESH_INTERVAL: Final = timedelta(hours=6)
BALANCE_REFRESH_COOLDOWN: Final = (
    60  # seconds after a request before re-reading balance
)

STUDIO_URL: Final = "https://developers.sber.ru/studio/workspaces"
