"""Tests for the GigaChat API client."""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.gigachat.api import (
    GigaChatAuthError,
    GigaChatClient,
    GigaChatConnectionError,
    GigaChatError,
    GigaChatModel,
    GigaChatPaymentRequiredError,
    GigaChatRateLimitError,
    GigaChatRequestError,
    GigaChatScopeError,
    GigaChatServerError,
    GigaChatSSLError,
    _iter_sse_events,
    client_id_from_credentials,
    normalize_credentials,
)

from .common import AUTH_KEY, CLIENT_ID, CLIENT_SECRET

AUTH_URL = "https://auth.example/api/v2/oauth"
BASE_URL = "https://api.example/v1"


@pytest.fixture(autouse=True)
def no_sleep() -> Any:
    """Do not wait between retries."""
    with patch("custom_components.gigachat.api.asyncio.sleep", AsyncMock()):
        yield


def _token(expires_in: float = 1800) -> dict[str, Any]:
    return {
        "access_token": "token",
        "expires_at": int((time.time() + expires_in) * 1000),
    }


def _client(hass: HomeAssistant) -> GigaChatClient:
    return GigaChatClient(
        async_get_clientsession(hass),
        AUTH_KEY,
        "GIGACHAT_API_PERS",
        base_url=BASE_URL,
        auth_url=AUTH_URL,
        ssl_context=False,
        user_agent="test",
    )


def _sequence(*responses: tuple[int, Any]) -> Any:
    """Return an aioclient_mock side effect answering with each response in turn."""
    queue = list(responses)

    async def side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        status, body = queue.pop(0)
        return AiohttpClientMockResponse(method, url, status=status, json=body)

    return side_effect


@pytest.mark.parametrize(
    "value",
    [
        AUTH_KEY,
        f"  {AUTH_KEY}\n",
        f"Basic {AUTH_KEY}",
        f"{AUTH_KEY[:10]}\n{AUTH_KEY[10:]}",
        f"{CLIENT_ID}:{CLIENT_SECRET}",
    ],
)
def test_normalize_credentials(value: str) -> None:
    """Test the accepted credential formats."""
    assert normalize_credentials(value) == AUTH_KEY
    assert client_id_from_credentials(normalize_credentials(value)) == CLIENT_ID


@pytest.mark.parametrize(
    "value",
    ["", "not a key", base64.b64encode(b"no-colon").decode(), ":secret", "id:"],
)
def test_normalize_credentials_invalid(value: str) -> None:
    """Test that invalid credentials are rejected."""
    with pytest.raises(ValueError):
        normalize_credentials(value)


async def test_token_is_cached_and_refreshed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that the access token is reused until it expires."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/models",
        json={"data": [{"id": "GigaChat-2", "type": "chat", "owned_by": "x"}, {}]},
    )
    client = _client(hass)

    assert await client.async_get_models() == [GigaChatModel("GigaChat-2", "chat", "x")]
    assert await client.async_get_models()
    auth_calls = [
        call for call in aioclient_mock.mock_calls if str(call[1]) == AUTH_URL
    ]
    assert len(auth_calls) == 1
    method, _, data, headers = auth_calls[0]
    assert method == "POST"
    assert data == {"scope": "GIGACHAT_API_PERS"}
    assert headers["Authorization"] == f"Basic {AUTH_KEY}"
    assert headers["RqUID"]

    # An expired token is renewed
    client._token_expires_at = time.time()
    await client.async_get_models()
    assert len([c for c in aioclient_mock.mock_calls if str(c[1]) == AUTH_URL]) == 2


async def test_api_headers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the headers sent to the API."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(f"{BASE_URL}/chat/completions", json={"choices": []})
    await _client(hass).async_chat({"model": "GigaChat-2"}, session_id="session")
    _, _, data, headers = aioclient_mock.mock_calls[-1]
    assert headers["Authorization"] == "Bearer token"
    assert headers["User-Agent"] == "test"
    assert headers["X-Session-ID"] == "session"
    assert data == {"model": "GigaChat-2", "stream": False}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (
            400,
            {"code": 7, "message": "scope from db not fully includes consumed scope"},
            GigaChatScopeError,
        ),
        (400, {"code": 5, "message": "scope is empty"}, GigaChatScopeError),
        (
            401,
            {"code": 6, "message": "credentials doesn't match db data"},
            GigaChatAuthError,
        ),
        (
            400,
            {"code": 4, "message": "Can't decode 'Authorization' header"},
            GigaChatAuthError,
        ),
        (429, {"message": "Too many requests"}, GigaChatRateLimitError),
        (500, {"message": "Internal"}, GigaChatServerError),
    ],
)
async def test_token_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict[str, Any],
    error: type[GigaChatError],
) -> None:
    """Test errors of the OAuth endpoint."""
    aioclient_mock.post(AUTH_URL, status=status, json=body)
    with pytest.raises(error) as exc_info:
        await _client(hass).async_get_access_token()
    assert exc_info.value.message == body["message"]


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (aiohttp.ClientConnectionError(), GigaChatConnectionError),
        (TimeoutError(), GigaChatConnectionError),
        (
            aiohttp.ClientSSLError(
                MagicMock(), OSError(1, "certificate verify failed")
            ),
            GigaChatSSLError,
        ),
    ],
)
async def test_token_connection_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    exception: Exception,
    error: type[GigaChatError],
) -> None:
    """Test network errors while requesting a token."""
    aioclient_mock.post(AUTH_URL, exc=exception)
    with pytest.raises(error):
        await _client(hass).async_get_access_token()


async def test_balance(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test reading the balance."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/balance",
        json={"balance": [{"usage": "GigaChat", "value": 100}, {"usage": "x"}]},
    )
    assert await _client(hass).async_get_balance() == {"GigaChat": 100}


async def test_balance_not_available(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that pay-as-you-go accounts have no balance."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/balance",
        status=403,
        json={"status": 403, "message": "Permission denied"},
    )
    assert await _client(hass).async_get_balance() is None


async def test_balance_unauthorized(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that 401 on the balance is an authentication error."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/balance", status=401, json={"message": "Unauthorized"}
    )
    with pytest.raises(GigaChatAuthError):
        await _client(hass).async_get_balance()


async def test_retry_on_rate_limit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that 429 and 5xx responses are retried."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/models",
        side_effect=_sequence(
            (429, {"message": "Too Many Requests"}),
            (503, {"message": "Unavailable"}),
            (200, {"data": [{"id": "GigaChat-2"}]}),
        ),
    )
    assert await _client(hass).async_get_models() == [GigaChatModel("GigaChat-2")]


async def test_rate_limit_gives_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that the client stops retrying eventually."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/models", status=429, json={"message": "Too Many Requests"}
    )
    with pytest.raises(GigaChatRateLimitError):
        await _client(hass).async_get_models()
    assert aioclient_mock.call_count == 5  # token + 1 try + 3 retries


async def test_unauthorized_refreshes_token_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that a 401 triggers a single token refresh."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/models",
        side_effect=_sequence(
            (401, {"message": "Unauthorized"}), (200, {"data": [{"id": "GigaChat-2"}]})
        ),
    )
    client = _client(hass)
    assert await client.async_get_models()
    assert len([c for c in aioclient_mock.mock_calls if str(c[1]) == AUTH_URL]) == 2

    aioclient_mock.clear_requests()
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(
        f"{BASE_URL}/models", status=401, json={"message": "Unauthorized"}
    )
    with pytest.raises(GigaChatAuthError):
        await client.async_get_models()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (402, GigaChatPaymentRequiredError),
        (404, GigaChatRequestError),
        (413, GigaChatRequestError),
        (422, GigaChatRequestError),
        (403, GigaChatAuthError),
    ],
)
async def test_request_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    error: type[GigaChatError],
) -> None:
    """Test the mapping of HTTP errors."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(
        f"{BASE_URL}/chat/completions", status=status, json={"message": "details"}
    )
    with pytest.raises(error) as exc_info:
        await _client(hass).async_chat({})
    assert exc_info.value.status == status
    assert exc_info.value.message == "details"


async def test_connection_errors_are_retried(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that connection errors are retried before failing."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(f"{BASE_URL}/models", exc=aiohttp.ClientConnectionError())
    with pytest.raises(GigaChatConnectionError):
        await _client(hass).async_get_models()
    assert aioclient_mock.call_count == 5


async def test_chat_stream(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test parsing a streamed response."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(
        f"{BASE_URL}/chat/completions",
        text=(
            'data: {"choices":[{"delta":{"content":"При","role":"assistant"}}]}\n\n'
            ": keep-alive\n\n"
            'data: {"choices":[{"delta":{"content":"вет"},"finish_reason":"stop"}]}\r\n\r\n'
            "data: [DONE]\n\n"
            'data: {"choices":[{"delta":{"content":"ignored"}}]}\n\n'
        ),
        headers={"Content-Type": "text/event-stream"},
    )
    chunks = [chunk async for chunk in _client(hass).async_chat_stream({"model": "m"})]
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["При", "вет"]
    assert aioclient_mock.mock_calls[-1][2] == {"model": "m", "stream": True}
    assert aioclient_mock.mock_calls[-1][3]["Accept"] == "text/event-stream"


async def test_chat_stream_json_fallback(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a non streamed answer to a streaming request."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(
        f"{BASE_URL}/chat/completions",
        json={"choices": [{"message": {}}]},
        headers={"Content-Type": "application/json"},
    )
    chunks = [chunk async for chunk in _client(hass).async_chat_stream({})]
    assert chunks == [{"choices": [{"message": {}}]}]


async def test_chat_stream_error_event(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an error reported inside the stream."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(
        f"{BASE_URL}/chat/completions",
        text='data: {"status": 422, "message": "Invalid params"}\n\n',
        headers={"Content-Type": "text/event-stream"},
    )
    with pytest.raises(GigaChatRequestError, match="Invalid params"):
        async for _ in _client(hass).async_chat_stream({}):
            pass


async def test_sse_parser_handles_split_chunks() -> None:
    """Test that events split across network chunks are reassembled."""
    raw = (
        'data: {"a": "Привет"}\n\n'
        "event: message\n"
        'data: {"b":\n'
        "data: 2}\n\n"
        'data: {"c": 3}'
    ).encode()
    pieces = [raw[i : i + 5] for i in range(0, len(raw), 5)]

    class FakeStream:
        async def iter_any(self) -> Any:
            for piece in pieces:
                yield piece

    events = [event async for event in _iter_sse_events(FakeStream())]
    assert events == [{"a": "Привет"}, {"b": 2}, {"c": 3}]


async def test_sse_parser_malformed() -> None:
    """Test a malformed event."""

    class FakeStream:
        async def iter_any(self) -> Any:
            yield b"data: {oops\n\n"

    with pytest.raises(GigaChatError, match="Malformed"):
        async for _ in _iter_sse_events(FakeStream()):
            pass


async def test_files(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Test uploading, downloading and deleting files."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(f"{BASE_URL}/files", json={"id": "file-1"})
    aioclient_mock.get(
        f"{BASE_URL}/files/file-1/content",
        content=b"\xff\xd8jpeg",
        headers={"Content-Type": "image/jpeg"},
    )
    aioclient_mock.post(f"{BASE_URL}/files/file-1/delete", json={"deleted": True})
    client = _client(hass)

    assert (
        await client.async_upload_file(b"data", "photo.jpg", "image/jpeg") == "file-1"
    )
    assert isinstance(aioclient_mock.mock_calls[-1][2], aiohttp.FormData)
    assert await client.async_download_file("file-1") == (b"\xff\xd8jpeg", "image/jpeg")
    await client.async_delete_file("file-1")


async def test_upload_without_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an upload response without a file ID."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.post(f"{BASE_URL}/files", json={})
    with pytest.raises(GigaChatError):
        await _client(hass).async_upload_file(b"data", "a.txt", "text/plain")


@pytest.mark.parametrize("file_id", ["../balance", "a/b", "", "x" * 200])
async def test_invalid_file_id(hass: HomeAssistant, file_id: str) -> None:
    """Test that file IDs cannot escape the files endpoint."""
    with pytest.raises(GigaChatRequestError):
        await _client(hass).async_download_file(file_id)
    with pytest.raises(GigaChatRequestError):
        await _client(hass).async_delete_file(file_id)


async def test_concurrency_limit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test that requests wait for a free slot."""
    aioclient_mock.post(AUTH_URL, json=_token())
    aioclient_mock.get(f"{BASE_URL}/models", json={"data": []})
    client = _client(hass)
    await client._semaphore.acquire()
    with (
        patch("custom_components.gigachat.api.QUEUE_TIMEOUT", 0.01),
        pytest.raises(GigaChatRateLimitError),
    ):
        await client.async_get_models()
    client._semaphore.release()
    assert await client.async_get_models() == []
