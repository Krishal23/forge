"""Every model call goes through here.

Order of work per call: budget check, redaction, cache lookup, then providers in
failover order with retries and a circuit breaker each. Every attempt is logged.
The gateway does not look at the answer; agents/base.py validates it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from collections.abc import Callable

from dotenv import load_dotenv

from forge.chaos import CORRUPTED_OUTPUT, Chaos
from forge.config import FORGE_HOME, PROJECT_ROOT, Budgets
from forge.llm.breaker import CircuitBreaker
from forge.llm.cache import CACHE_MODES, ResponseCache
from forge.llm.providers import MockProvider, Provider, Responder, providers_from_env
from forge.llm.types import (
    AllProvidersFailed,
    BudgetExceeded,
    LLMError,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ReplayMiss,
)
from forge.redact import redact_messages
from forge.store import Store
from forge.util import monotonic_ms

log = logging.getLogger(__name__)

MAX_RETRY_AFTER_S = 45.0
QUOTA_EXHAUSTED_S = 120.0  # longer waits mean a daily quota: skip the provider


class Gateway:
    def __init__(
        self,
        providers: list[Provider],
        store: Store | None = None,
        cache: ResponseCache | None = None,
        cache_mode: str = "off",
        chaos: Chaos | None = None,
        budgets: Budgets | None = None,
        max_attempts_per_provider: int = 2,
        backoff_base_s: float = 0.5,
        breaker_factory: Callable[[], CircuitBreaker] = CircuitBreaker,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if cache_mode not in CACHE_MODES:
            raise ValueError(f"cache_mode must be one of {CACHE_MODES}")
        if cache_mode != "off" and cache is None:
            raise ValueError("a cache is required when cache_mode is not 'off'")
        self.providers = providers
        self.store = store
        self.cache = cache
        self.cache_mode = cache_mode
        self.chaos = chaos
        self.budgets = budgets
        self.max_attempts = max_attempts_per_provider
        self.backoff_base_s = backoff_base_s
        self.breakers = {p.name: breaker_factory() for p in providers}
        self._sleep = sleep

    def status(self) -> list[dict]:
        return [
            {
                "name": p.name,
                "model": p.model,
                "down_injected": bool(self.chaos and self.chaos.is_down(p.name)),
                **self.breakers[p.name].snapshot(),
            }
            for p in self.providers
        ]

    def complete(self, req: LLMRequest) -> LLMResponse:
        self._check_budget(req)
        messages, redactions = redact_messages(req.messages)
        if redactions:
            self._event(req, "redaction", {"counts": redactions})
        req = dataclasses.replace(req, messages=messages)
        key = req.cache_key()

        if self.cache_mode != "off":
            hit = self.cache.get(key)  # type: ignore[union-attr]
            if hit is not None:
                resp = LLMResponse(
                    text=hit["text"],
                    provider=hit["provider"],
                    model=hit["model"],
                    prompt_tokens=hit["prompt_tokens"],
                    completion_tokens=hit["completion_tokens"],
                    latency_ms=0.0,
                    cost_usd=0.0,
                    cache_hit=True,
                )
                self._record(req, resp.provider, resp.model, 1, "ok", None, resp, request_payload=messages)
                return self._post(req, resp)
            if self.cache_mode == "replay_only":
                self._event(req, "replay_miss", {"key": key[:16]})
                raise ReplayMiss(f"no recorded response for {req.agent} request {key[:16]}")

        errors: list[str] = []
        failed_attempts = 0
        for provider in self.providers:
            breaker = self.breakers[provider.name]
            if not breaker.allow():
                errors.append(f"{provider.name}: circuit open")
                self._event(req, "provider_skipped", {"provider": provider.name, "reason": "circuit open"})
                continue
            for attempt in range(1, self.max_attempts + 1):
                t0 = monotonic_ms()
                try:
                    if self.chaos and self.chaos.is_down(provider.name):
                        raise ProviderError(provider.name, "HTTP 503 (injected: provider down)", True, status=503)
                    completion = provider.complete(req)
                except ProviderError as exc:
                    latency = monotonic_ms() - t0
                    failed_attempts += 1
                    errors.append(str(exc))
                    if exc.status == 429 and exc.retry_after and exc.retry_after > QUOTA_EXHAUSTED_S:
                        breaker.trip(min(exc.retry_after, 3600))
                        opened = True
                        self._event(req, "quota_exhausted", {"provider": provider.name,
                                                             "retry_after_s": exc.retry_after})
                    else:
                        opened = breaker.record_failure()
                    self._record(req, provider.name, provider.model, attempt, "error", exc.message, None,
                                 latency_ms=latency, request_payload=messages)
                    if opened:
                        self._event(req, "circuit_open", {"provider": provider.name, "cooldown_s": breaker.cooldown_s})
                    if not exc.retryable or breaker.state == "open" or attempt == self.max_attempts:
                        break
                    delay = (
                        min(exc.retry_after, MAX_RETRY_AFTER_S)
                        if exc.retry_after is not None
                        else self.backoff_base_s * 2 ** (attempt - 1)
                    )
                    self._event(req, "llm_retry", {"provider": provider.name, "attempt": attempt, "delay_s": delay,
                                                   "error": exc.message[:200]})
                    self._sleep(delay)
                    continue

                latency = monotonic_ms() - t0
                breaker.record_success()
                resp = LLMResponse(
                    text=completion.text,
                    provider=provider.name,
                    model=provider.model,
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                    latency_ms=latency,
                    cost_usd=provider.pricing.cost(completion.prompt_tokens, completion.completion_tokens),
                    failed_attempts=failed_attempts,
                )
                if self.cache_mode == "read_write":
                    self.cache.put(key, {  # type: ignore[union-attr]
                        "agent": req.agent,
                        "text": resp.text,
                        "provider": resp.provider,
                        "model": resp.model,
                        "prompt_tokens": resp.prompt_tokens,
                        "completion_tokens": resp.completion_tokens,
                    })
                self._record(req, provider.name, provider.model, attempt, "ok", None, resp, request_payload=messages)
                if failed_attempts:
                    self._event(req, "provider_failover" if provider is not self.providers[0] else "llm_recovered", {
                        "to": provider.name,
                        "failed_attempts": failed_attempts,
                        "errors": [e[:200] for e in errors[-3:]],
                    })
                return self._post(req, resp)

        self._event(req, "all_providers_failed", {"errors": [e[:200] for e in errors[-6:]]})
        raise AllProvidersFailed(errors)

    def _check_budget(self, req: LLMRequest) -> None:
        if not (self.store and self.budgets and req.run_id):
            return
        calls, cost = self.store.llm_totals(req.run_id)
        if calls >= self.budgets.max_llm_calls:
            raise BudgetExceeded(f"LLM call budget reached ({calls}/{self.budgets.max_llm_calls})")
        if cost >= self.budgets.max_cost_usd:
            raise BudgetExceeded(f"cost budget reached (${cost:.4f}/${self.budgets.max_cost_usd:.2f})")

    def _post(self, req: LLMRequest, resp: LLMResponse) -> LLMResponse:
        if self.chaos and self.chaos.take("corrupt_next"):
            self._event(req, "chaos_injected", {"fault": "corrupt_output", "provider": resp.provider})
            resp = dataclasses.replace(resp, text=CORRUPTED_OUTPUT)
        return resp

    def _event(self, req: LLMRequest, type: str, data: dict) -> None:
        if self.store and req.run_id:
            self.store.append_event(req.run_id, type, data, stage=req.stage, agent=req.agent)

    def _record(
        self,
        req: LLMRequest,
        provider: str,
        model: str,
        attempt: int,
        status: str,
        error: str | None,
        resp: LLMResponse | None,
        latency_ms: float | None = None,
        request_payload: list[dict] | None = None,
    ) -> None:
        if not self.store:
            return
        fields = {
            "run_id": req.run_id,
            "stage": req.stage,
            "agent": req.agent,
            "provider": provider,
            "model": model,
            "attempt": attempt,
            "status": status,
            "error": (error or "")[:500] or None,
            "prompt_tokens": resp.prompt_tokens if resp else 0,
            "completion_tokens": resp.completion_tokens if resp else 0,
            "latency_ms": round(resp.latency_ms if resp else (latency_ms or 0.0), 1),
            "cost_usd": resp.cost_usd if resp else 0.0,
            "cache_hit": int(bool(resp and resp.cache_hit)),
            "request_json": json.dumps(request_payload or []),
            "response_text": resp.text if resp else None,
        }
        self.store.record_llm_call(**fields)
        self._event(req, "llm_call", {k: v for k, v in fields.items()
                                      if k not in {"run_id", "stage", "agent", "request_json", "response_text"}})


def build_gateway(
    mode: str,
    store: Store | None = None,
    chaos: Chaos | None = None,
    responder: Responder | None = None,
    budgets: Budgets | None = None,
    cache_mode: str | None = None,
    mock_latency_ms: float | None = None,
) -> Gateway:
    """Create a gateway for a run mode.

    mock   - two offline mock providers (so failover works without network); no cache by default
    live   - real providers from .env in FORGE_PROVIDERS order; read_write cache by default
    replay - serve only recorded responses from the cache; never calls a provider
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    cache = ResponseCache(FORGE_HOME / "llm-cache")
    if mode == "mock":
        latency = float(os.environ.get("FORGE_MOCK_LATENCY_MS", "600")) if mock_latency_ms is None else mock_latency_ms
        providers: list[Provider] = [
            MockProvider("mock-primary", "mock-large", responder, latency_ms=latency),
            MockProvider("mock-secondary", "mock-small", responder, latency_ms=latency * 0.5),
        ]
        return Gateway(providers, store, cache, cache_mode or "off", chaos, budgets)
    if mode == "live":
        live = providers_from_env()
        if not live:
            raise LLMError("live mode needs at least one API key in .env (GEMINI_API_KEY, GROQ_API_KEY or OPENROUTER_API_KEY)")
        return Gateway(live, store, cache, cache_mode or os.environ.get("FORGE_CACHE_MODE", "read_write"), chaos, budgets)
    if mode == "replay":
        return Gateway([], store, cache, "replay_only", chaos, budgets)
    raise ValueError(f"unknown mode {mode!r}")
