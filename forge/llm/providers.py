"""Model providers.

Gemini, Groq and OpenRouter all accept the OpenAI chat format, so one client covers them.
Prices are paid-tier list prices used only for the cost estimate; free tiers cost nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from forge.llm.types import Completion, LLMRequest, ProviderError, estimate_tokens


@dataclass(frozen=True)
class Pricing:
    input_per_m: float
    output_per_m: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.input_per_m + completion_tokens * self.output_per_m) / 1_000_000


class Provider(Protocol):
    name: str
    model: str
    pricing: Pricing

    def complete(self, req: LLMRequest) -> Completion: ...


@dataclass(frozen=True)
class ProviderSpec:
    base_url: str
    key_env: str
    model_env: str
    default_model: str
    pricing: Pricing
    supports_json_mode: bool


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_env="GEMINI_API_KEY",
        model_env="GEMINI_MODEL",
        default_model="gemini-3.5-flash",
        pricing=Pricing(0.30, 2.50),
        supports_json_mode=True,
    ),
    "groq": ProviderSpec(
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
        default_model="openai/gpt-oss-120b",
        pricing=Pricing(0.15, 0.60),
        supports_json_mode=True,
    ),
    "openrouter": ProviderSpec(
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        default_model="meta-llama/llama-3.3-70b-instruct:free",
        pricing=Pricing(0.59, 0.79),
        supports_json_mode=False,
    ),
}

_RETRYABLE_STATUS = {408, 409, 425, 429}


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        pricing: Pricing,
        supports_json_mode: bool = True,
        timeout_s: float = 90.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.name = name
        self.model = model
        self.pricing = pricing
        self.supports_json_mode = supports_json_mode
        self._url = base_url.rstrip("/") + "/chat/completions"
        # The key is only kept in this header, never in logs or errors.
        self._client = httpx.Client(
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    def complete(self, req: LLMRequest) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.json_mode and self.supports_json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.post(self._url, json=body)
        except httpx.TimeoutException:
            raise ProviderError(self.name, "request timed out", retryable=True) from None
        except httpx.TransportError as exc:
            raise ProviderError(self.name, f"network error ({type(exc).__name__})", retryable=True) from None

        if resp.status_code >= 400:
            retryable = resp.status_code in _RETRYABLE_STATUS or resp.status_code >= 500
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=retryable,
                status=resp.status_code,
                retry_after=_retry_after(resp),
            )
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError(self.name, "unexpected response shape", retryable=True) from None
        usage = data.get("usage") or {}
        prompt_chars = sum(len(str(m.get("content", ""))) for m in req.messages)
        return Completion(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens") or estimate_tokens("x" * prompt_chars)),
            completion_tokens=int(usage.get("completion_tokens") or estimate_tokens(text)),
        )


_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def _retry_after(resp: httpx.Response) -> float | None:
    """Seconds to wait, from the Retry-After header or Google's retryDelay field in the body."""
    value = resp.headers.get("retry-after")
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = _RETRY_DELAY.search(resp.text or "")
    return float(m.group(1)) if m else None


Responder = Callable[[LLMRequest], "str | dict"]


class MockProvider:
    """Offline provider. `responder` maps a request to the model's text (or a dict, serialised as JSON)."""

    def __init__(
        self,
        name: str,
        model: str,
        responder: Responder | None,
        pricing: Pricing = PROVIDER_SPECS["gemini"].pricing,
        latency_ms: float = 0.0,
    ):
        self.name = name
        self.model = model
        self.pricing = pricing
        self._responder = responder
        self._latency_ms = latency_ms

    def complete(self, req: LLMRequest) -> Completion:
        if self._responder is None:
            raise ProviderError(self.name, "no mock responder configured", retryable=False)
        if self._latency_ms:
            h = int(hashlib.sha256(req.cache_key().encode()).hexdigest()[:4], 16) / 0xFFFF
            time.sleep(self._latency_ms * (0.6 + 0.8 * h) / 1000)
        out = self._responder(req)
        text = out if isinstance(out, str) else json.dumps(out, indent=2)
        prompt = "".join(str(m.get("content", "")) for m in req.messages)
        return Completion(text=text, prompt_tokens=estimate_tokens(prompt), completion_tokens=estimate_tokens(text))


def providers_from_env(order: list[str] | None = None) -> list[OpenAICompatProvider]:
    """Build live providers in failover order, skipping any without an API key."""
    if order is None:
        order = [p.strip() for p in os.environ.get("FORGE_PROVIDERS", "gemini,groq,openrouter").split(",")]
    providers = []
    for entry in order:
        # "gemini:<model>" pins a model. Free quota is per model, so listing several helps.
        name, _, model = entry.partition(":")
        spec = PROVIDER_SPECS.get(name)
        if spec is None:
            continue
        key = os.environ.get(spec.key_env, "").strip()
        if not key:
            continue
        model = model.strip() or os.environ.get(spec.model_env, "").strip() or spec.default_model
        providers.append(
            OpenAICompatProvider(
                name=f"{name}:{model}" if ":" in entry else name,
                base_url=spec.base_url,
                api_key=key,
                model=model,
                pricing=spec.pricing,
                supports_json_mode=spec.supports_json_mode,
            )
        )
    return providers
