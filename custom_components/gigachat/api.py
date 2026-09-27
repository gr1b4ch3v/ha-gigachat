"""Asynchronous client for the GigaChat REST API.

Handles OAuth access tokens, the per-account concurrency limit, retries and
server-sent event streaming. The client only depends on aiohttp so it can use
the shared Home Assistant session.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import logging
import re
import ssl
import time
from typing import Any
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

TOKEN_EXPIRY_MARGIN = 60  # seconds
AUTH_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=15)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)
CHAT_TIMEOUT = aiohttp.ClientTimeout(total=180, connect=15)
# Maximum silence between two stream events. Image generation can stay silent
# for about half a minute when the image service times out, text never does.
STREAM_READ_TIMEOUT = 60.0
IMAGE_STREAM_READ_TIMEOUT = 120.0
FILE_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=15)
RETRY_DELAYS = (1.0, 2.0, 4.0)
MAX_RETRY_AFTER = 10.0
QUEUE_TIMEOUT = 150  # seconds to wait for a free stream

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class GigaChatError(Exception):
    """Base error for the GigaChat API."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Initialize the error."""
        super().__init__(message)
        self.message = message
        self.status = status


class GigaChatConnectionError(GigaChatError):
    """The API could not be reached."""


class GigaChatSSLError(GigaChatConnectionError):
    """The TLS certificate of the API could not be verified."""


class GigaChatAuthError(GigaChatError):
    """The credentials were rejected."""


class GigaChatScopeError(GigaChatAuthError):
    """The authorization key does not belong to the requested API scope."""


class GigaChatPaymentRequiredError(GigaChatError):
    """The token package of the requested model is exhausted."""


class GigaChatRateLimitError(GigaChatError):
    """Too many concurrent requests."""


class GigaChatRequestError(GigaChatError):
    """The API rejected the request (validation error, unknown model, ...)."""


class GigaChatServerError(GigaChatError):
    """The API failed to process the request."""


@dataclass(frozen=True, slots=True)
class GigaChatModel:
    """A model offered by the API."""

    id: str
    type: str | None = None
    owned_by: str | None = None


def normalize_credentials(value: str) -> str:
    """Return the authorization key for a user supplied credential string.

    Accepts the authorization key shown in GigaChat Studio (base64 encoded
    "client_id:client_secret"), optionally prefixed with "Basic", or the raw
    "client_id:client_secret" pair. Raises ValueError for anything else.
    """
    key = value.strip()
    if key[:6].lower() == "basic ":
        key = key[6:]
    key = "".join(key.split())
    if ":" in key:
        client_id, _, secret = key.partition(":")
        if not client_id or not secret:
            raise ValueError("Expected client_id:client_secret")
        return base64.b64encode(key.encode()).decode()
    try:
        decoded = base64.b64decode(key, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as err:
        raise ValueError("Authorization key is not valid base64") from err
    client_id, separator, secret = decoded.partition(":")
    if not separator or not client_id or not secret:
        raise ValueError("Authorization key does not contain a client secret")
    return key


def client_id_from_credentials(credentials: str) -> str:
    """Return the client ID encoded in an authorization key."""
    return base64.b64decode(credentials).decode().partition(":")[0]


class GigaChatClient:
    """Client for the GigaChat REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        credentials: str,
        scope: str,
        *,
        base_url: str,
        auth_url: str,
        ssl_context: ssl.SSLContext | bool,
        user_agent: str,
        max_concurrent_requests: int = 1,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._credentials = credentials
        self._scope = scope
        self._base_url = base_url.rstrip("/")
        self._auth_url = auth_url
        self._ssl = ssl_context
        self._user_agent = user_agent
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def scope(self) -> str:
        """Return the API scope."""
        return self._scope

    async def async_get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, requesting a new one when needed."""
        async with self._token_lock:
            if (
                not force_refresh
                and self._token
                and time.time() < self._token_expires_at - TOKEN_EXPIRY_MARGIN
            ):
                return self._token

            headers = {
                "Authorization": f"Basic {self._credentials}",
                "RqUID": str(uuid.uuid4()),
                "Accept": "application/json",
                "User-Agent": self._user_agent,
            }
            try:
                async with self._session.post(
                    self._auth_url,
                    data={"scope": self._scope},
                    headers=headers,
                    ssl=self._ssl,
                    timeout=AUTH_TIMEOUT,
                ) as resp:
                    status = resp.status
                    body = await _read_json(resp)
            except aiohttp.ClientSSLError as err:
                raise GigaChatSSLError(str(err)) from err
            except (aiohttp.ClientError, TimeoutError) as err:
                raise GigaChatConnectionError(str(err) or type(err).__name__) from err

            if status == 200 and body.get("access_token"):
                self._token = body["access_token"]
                if expires_at := body.get("expires_at"):
                    self._token_expires_at = float(expires_at) / 1000
                else:
                    self._token_expires_at = time.time() + float(
                        body.get("expires_in", 1800)
                    )
                return self._token

            message = _error_message(body, status)
            # Codes documented for the OAuth endpoint: 1 invalid scope format,
            # 5 empty scope, 7 key does not belong to the requested scope.
            if body.get("code") in (1, 5, 7):
                raise GigaChatScopeError(message, status)
            if status in (400, 401, 403):
                raise GigaChatAuthError(message, status)
            raise _error_for_status(status, message)

    async def async_get_models(self) -> list[GigaChatModel]:
        """Return the models available to the account."""
        async with self._async_response("GET", "/models") as resp:
            body = await _read_json(resp)
        return [
            GigaChatModel(
                id=item["id"], type=item.get("type"), owned_by=item.get("owned_by")
            )
            for item in body.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]

    async def async_get_balance(self) -> dict[str, float] | None:
        """Return the remaining prepaid tokens per model family.

        Returns None for pay-as-you-go accounts, which have no balance.
        """
        try:
            async with self._async_response("GET", "/balance") as resp:
                body = await _read_json(resp)
        except GigaChatAuthError as err:
            if err.status == 403:
                return None
            raise
        return {
            str(item["usage"]): item["value"]
            for item in body.get("balance", [])
            if isinstance(item, dict) and "usage" in item and "value" in item
        }

    async def async_chat(
        self, payload: dict[str, Any], *, session_id: str | None = None
    ) -> dict[str, Any]:
        """Generate a complete (non streaming) response."""
        async with self._async_response(
            "POST",
            "/chat/completions",
            json_data={**payload, "stream": False},
            session_id=session_id,
            client_timeout=CHAT_TIMEOUT,
        ) as resp:
            return await _read_json(resp)

    async def async_chat_stream(
        self,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        read_timeout: float = STREAM_READ_TIMEOUT,
    ) -> AsyncIterator[dict[str, Any]]:
        """Generate a response, yielding the streamed chunks as they arrive.

        The concurrency slot is held until the generator is exhausted or closed,
        so callers should wrap it in contextlib.aclosing().
        """
        async with self._async_response(
            "POST",
            "/chat/completions",
            json_data={**payload, "stream": True},
            session_id=session_id,
            client_timeout=aiohttp.ClientTimeout(
                total=None, connect=15, sock_read=read_timeout
            ),
            accept="text/event-stream",
        ) as resp:
            if resp.content_type == "application/json":
                # Not a stream after all, e.g. an intermediary buffered it.
                yield await _read_json(resp)
                return
            try:
                async for event in _iter_sse_events(resp.content):
                    if "choices" not in event and "message" in event:
                        raise _error_for_status(
                            int(event.get("status") or 500),
                            _error_message(event, 500),
                        )
                    yield event
            except (aiohttp.ClientError, TimeoutError) as err:
                raise GigaChatConnectionError(str(err) or type(err).__name__) from err

    async def async_upload_file(
        self, data: bytes, filename: str, content_type: str
    ) -> str:
        """Upload a file to the storage and return its ID."""

        def build_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field("file", data, filename=filename, content_type=content_type)
            form.add_field("purpose", "general")
            return form

        async with self._async_response(
            "POST", "/files", data_factory=build_form, client_timeout=FILE_TIMEOUT
        ) as resp:
            body = await _read_json(resp)
        if not (file_id := body.get("id")):
            raise GigaChatError("File upload did not return an ID")
        return str(file_id)

    async def async_download_file(self, file_id: str) -> tuple[bytes, str | None]:
        """Download a file from the storage. Returns the data and its MIME type."""
        _validate_file_id(file_id)
        async with self._async_response(
            "GET",
            f"/files/{file_id}/content",
            accept="*/*",
            client_timeout=FILE_TIMEOUT,
        ) as resp:
            return await resp.read(), resp.content_type

    async def async_delete_file(self, file_id: str) -> None:
        """Delete a file from the storage."""
        _validate_file_id(file_id)
        async with self._async_response("POST", f"/files/{file_id}/delete"):
            pass

    @asynccontextmanager
    async def _async_response(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
        data_factory: Callable[[], Any] | None = None,
        session_id: str | None = None,
        accept: str = "application/json",
        client_timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Perform a request and yield the successful response.

        Waits for a free concurrency slot and holds it until the response has
        been consumed.
        """
        try:
            async with asyncio.timeout(QUEUE_TIMEOUT):
                await self._semaphore.acquire()
        except TimeoutError as err:
            raise GigaChatRateLimitError(
                "Timed out waiting for a free GigaChat request slot"
            ) from err
        try:
            resp = await self._async_send(
                method,
                path,
                json_data=json_data,
                data_factory=data_factory,
                session_id=session_id,
                accept=accept,
                client_timeout=client_timeout,
            )
            try:
                yield resp
            finally:
                resp.release()
        finally:
            self._semaphore.release()

    async def _async_send(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None,
        data_factory: Callable[[], Any] | None,
        session_id: str | None,
        accept: str,
        client_timeout: aiohttp.ClientTimeout,
    ) -> aiohttp.ClientResponse:
        """Send a request, retrying on rate limits and transient failures."""
        url = f"{self._base_url}{path}"
        attempt = 0
        token_refreshed = False
        while True:
            token = await self.async_get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": accept,
                "User-Agent": self._user_agent,
                "X-Request-ID": str(uuid.uuid4()),
            }
            if session_id:
                headers["X-Session-ID"] = session_id
            try:
                resp = await self._session.request(
                    method,
                    url,
                    json=json_data,
                    data=data_factory() if data_factory else None,
                    headers=headers,
                    ssl=self._ssl,
                    timeout=client_timeout,
                )
            except aiohttp.ClientSSLError as err:
                raise GigaChatSSLError(str(err)) from err
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt < len(RETRY_DELAYS):
                    _LOGGER.debug("Request to %s failed (%r), retrying", path, err)
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    attempt += 1
                    continue
                raise GigaChatConnectionError(str(err) or type(err).__name__) from err

            if resp.status == 200:
                return resp

            body = await _read_json(resp)
            resp.release()
            message = _error_message(body, resp.status)

            if resp.status == 401 and not token_refreshed:
                # The token may have been revoked before its expiry time.
                token_refreshed = True
                await self.async_get_access_token(force_refresh=True)
                continue
            if (resp.status == 429 or resp.status >= 500) and attempt < len(
                RETRY_DELAYS
            ):
                delay = _retry_after(resp.headers) or RETRY_DELAYS[attempt]
                _LOGGER.debug(
                    "Request to %s returned %s (%s), retrying in %.1fs",
                    path,
                    resp.status,
                    message,
                    delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            raise _error_for_status(resp.status, message)


async def _iter_sse_events(
    stream: aiohttp.StreamReader,
) -> AsyncIterator[dict[str, Any]]:
    """Parse a server-sent event stream into JSON payloads, until [DONE]."""
    buffer = b""
    data_lines: list[str] = []

    def process_line(raw: bytes) -> str | None:
        """Handle one line; return the event data once an event is complete."""
        line = raw.rstrip(b"\r").decode("utf-8")
        if not line:
            if not data_lines:
                return None
            data = "\n".join(data_lines)
            data_lines.clear()
            return data
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value.removeprefix(" "))
        return None

    async for chunk in stream.iter_any():
        buffer += chunk
        while (newline := buffer.find(b"\n")) >= 0:
            raw, buffer = buffer[:newline], buffer[newline + 1 :]
            if (data := process_line(raw)) is None:
                continue
            if data.strip() == "[DONE]":
                return
            yield _parse_event(data)

    # Flush an event that was not terminated by a blank line.
    if buffer:
        process_line(buffer)
    if (data := process_line(b"")) is not None and data.strip() != "[DONE]":
        yield _parse_event(data)


def _parse_event(data: str) -> dict[str, Any]:
    """Decode the JSON payload of a stream event."""
    try:
        event = json.loads(data)
    except ValueError as err:
        raise GigaChatError(f"Malformed stream event: {data[:200]}") from err
    if not isinstance(event, dict):
        raise GigaChatError(f"Unexpected stream event: {data[:200]}")
    return event


async def _read_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """Read a JSON body, tolerating wrong content types and non-JSON errors."""
    try:
        body = await resp.json(content_type=None)
    except (ValueError, aiohttp.ClientError):
        try:
            text = await resp.text()
        except (ValueError, aiohttp.ClientError):
            text = ""
        return {"message": text.strip()[:300]} if text else {}
    return body if isinstance(body, dict) else {"data": body}


def _error_message(body: dict[str, Any], status: int) -> str:
    """Extract a human readable message from an error body."""
    message = body.get("message") or body.get("error_description") or body.get("error")
    return str(message) if message else f"HTTP {status}"


def _error_for_status(status: int, message: str) -> GigaChatError:
    """Map an HTTP status to an exception."""
    if status in (401, 403):
        return GigaChatAuthError(message, status)
    if status == 402:
        return GigaChatPaymentRequiredError(message, status)
    if status == 429:
        return GigaChatRateLimitError(message, status)
    if status >= 500:
        return GigaChatServerError(message, status)
    return GigaChatRequestError(message, status)


def _retry_after(headers: Any) -> float | None:
    """Return the delay requested by a Retry-After header, if any."""
    try:
        value = float(headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None
    return min(max(value, 0.0), MAX_RETRY_AFTER)


def _validate_file_id(file_id: str) -> None:
    """Guard against path injection through file IDs returned by the model."""
    if not _FILE_ID_RE.match(file_id):
        raise GigaChatRequestError(f"Invalid file ID: {file_id!r}")
