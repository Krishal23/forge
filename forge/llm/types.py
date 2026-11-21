"""Request, response and error types for the LLM gateway."""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.util import hash_obj


@dataclass
class LLMRequest:
    messages: list[dict]
    agent: str
    run_id: str | None = None
    stage: str | None = None
    temperature: float = 0.1
    json_mode: bool = True
    max_tokens: int = 8192
    meta: dict = field(default_factory=dict)  # only used by the mock responder

    def cache_key(self) -> str:
        """Provider-independent key. Excludes run_id so identical requests replay across runs."""
        return hash_obj(
            {
                "agent": self.agent,
                "messages": self.messages,
                "temperature": self.temperature,
                "json": self.json_mode,
                "max_tokens": self.max_tokens,
            }
        )


@dataclass
class Completion:
    """What a provider returns before the gateway adds metering."""

    text: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    cost_usd: float
    cache_hit: bool = False
    failed_attempts: int = 0


class LLMError(Exception):
    pass


class ProviderError(LLMError):
    def __init__(
        self, provider: str, message: str, retryable: bool, status: int | None = None, retry_after: float | None = None
    ):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


class AllProvidersFailed(LLMError):
    def __init__(self, errors: list[str]):
        super().__init__("all providers failed: " + "; ".join(errors[-4:]))
        self.errors = errors


class BudgetExceeded(LLMError):
    pass


class ReplayMiss(LLMError):
    pass


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token) when a provider omits usage."""
    return max(1, len(text) // 4)
