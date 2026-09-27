"""Helpers for the GigaChat tests."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Callable
import copy
from typing import Any

CLIENT_ID = "01a0e189-0000-0000-0000-000000000000"
CLIENT_SECRET = "secret-0000"
AUTH_KEY = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 30,
    "total_tokens": 100,
    "precached_prompt_tokens": 50,
}


def text_chunks(*parts: str, usage: dict[str, int] | None = None) -> list[dict]:
    """Return the stream chunks of a text answer."""
    chunks: list[dict[str, Any]] = [
        {"choices": [{"delta": {"content": part, "role": "assistant"}, "index": 0}]}
        for part in parts
    ]
    chunks.append(
        {
            "choices": [
                {"delta": {"content": ""}, "index": 0, "finish_reason": "stop"}
            ],
            "usage": usage or USAGE,
        }
    )
    return chunks


def function_call_chunks(
    name: str,
    arguments: Any,
    *,
    call_id: str = "call-1",
    state_id: str = "state-1",
) -> list[dict]:
    """Return the stream chunks of a function call."""
    return [
        {
            "choices": [
                {
                    "delta": {
                        "content": "",
                        "role": "assistant",
                        "function_call": {
                            "id": call_id,
                            "name": name,
                            "arguments": arguments,
                        },
                        "functions_state_id": state_id,
                    },
                    "index": 0,
                    "finish_reason": "function_call",
                }
            ],
            "usage": USAGE,
        }
    ]


def mock_stream(
    *responses: list[dict] | Exception,
) -> tuple[Callable[..., AsyncIterator[dict]], list[dict[str, Any]]]:
    """Return a fake async_chat_stream and the list of payloads it received."""
    payloads: list[dict[str, Any]] = []
    queue = list(responses)

    async def stream(
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        read_timeout: float | None = None,
    ) -> AsyncIterator[dict]:
        payloads.append(
            {
                **copy.deepcopy(payload),
                "_session_id": session_id,
                "_read_timeout": read_timeout,
            }
        )
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        for chunk in response:
            yield chunk

    return stream, payloads
