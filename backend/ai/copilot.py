"""Provider-neutral AI completion boundary.

Only deployment-controlled URLs and credentials reach this module. User input
cannot choose a provider URL, add headers, or make arbitrary network requests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_AI_OUTPUT_BYTES = 65_536


class CopilotProviderError(RuntimeError):
    """Safe provider failure whose message contains no response body or secret."""


@dataclass(frozen=True)
class CopilotResponse:
    text: str
    usage: dict[str, int]


class AIProvider(Protocol):
    name: str
    model: str

    def complete(self, *, system_prompt: str, user_prompt: str) -> CopilotResponse: ...


class HTTPTransport(Protocol):
    def __call__(self, request: Request, timeout: float) -> bytes: ...


def _urlopen_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is deployment configuration only
        body = response.read(MAX_AI_OUTPUT_BYTES + 1)
    if len(body) > MAX_AI_OUTPUT_BYTES:
        raise CopilotProviderError("AI provider response exceeded the configured limit")
    return body


class HTTPChatProvider:
    """OpenAI-compatible HTTP adapter behind the vendor-neutral provider protocol."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30,
        transport: HTTPTransport | None = None,
    ) -> None:
        parsed_endpoint = urlparse(endpoint)
        secure_endpoint = parsed_endpoint.scheme == "https" and bool(parsed_endpoint.hostname)
        local_endpoint = (
            parsed_endpoint.scheme == "http" and parsed_endpoint.hostname in {"127.0.0.1", "::1", "localhost"}
        )
        if not secure_endpoint and not local_endpoint:
            raise ValueError("AI provider endpoint must use HTTPS or a loopback address")
        if not api_key or not model:
            raise ValueError("AI provider key and model are required")
        self.endpoint = endpoint
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = min(max(float(timeout_seconds), 1.0), 60.0)
        self._transport = transport or _urlopen_transport

    def complete(self, *, system_prompt: str, user_prompt: str) -> CopilotResponse:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }, separators=(",", ":")).encode()
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        try:
            raw = self._transport(request, self.timeout_seconds)
            payload = json.loads(raw)
            text = payload["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty content")
            encoded = text.encode()
            if len(encoded) > MAX_AI_OUTPUT_BYTES:
                raise CopilotProviderError("AI provider response exceeded the configured limit")
            raw_usage = payload.get("usage") if isinstance(payload, dict) else None
            usage = {
                str(key): int(value)
                for key, value in (raw_usage.items() if isinstance(raw_usage, dict) else [])
                if isinstance(value, int) and value >= 0
            }
            return CopilotResponse(text=text, usage=usage)
        except CopilotProviderError:
            raise
        except (HTTPError, URLError, TimeoutError) as error:
            raise CopilotProviderError("AI provider request failed") from error
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CopilotProviderError("AI provider returned an invalid response") from error
