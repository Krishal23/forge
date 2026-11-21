import json

import httpx
import pytest

from forge.chaos import CORRUPTED_OUTPUT, Chaos
from forge.config import Budgets
from forge.llm import AllProvidersFailed, BudgetExceeded, Gateway, LLMRequest, ProviderError, ReplayMiss
from forge.llm.breaker import CircuitBreaker
from forge.llm.cache import ResponseCache
from forge.llm.providers import MockProvider, OpenAICompatProvider, Pricing
from forge.llm.types import Completion
from forge.store import Store

RUN = "run_1"


@pytest.fixture
def store():
    s = Store(":memory:")
    s.create_run(RUN, "t", "r", "mock", "ANALYSIS")
    yield s
    s.close()


def req(text="hello, reply in json", agent="analyst"):
    return LLMRequest(messages=[{"role": "user", "content": text}], agent=agent, run_id=RUN, stage="ANALYSIS")


class Flaky:
    """Provider that fails `fail_times` times, then succeeds."""

    def __init__(self, name, fail_times=0, retryable=True, status=503):
        self.name, self.model, self.pricing = name, f"{name}-model", Pricing(1.0, 2.0)
        self.fail_times, self.retryable, self.status, self.calls = fail_times, retryable, status, 0

    def complete(self, r):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(self.name, f"HTTP {self.status}", self.retryable, status=self.status)
        return Completion(text='{"ok": true}', prompt_tokens=1000, completion_tokens=500)


def gw(providers, store, **kw):
    return Gateway(providers, store=store, sleep=lambda s: None, **kw)


def types(store):
    return [e["type"] for e in store.events(RUN)]


def test_success_is_metered(store):
    resp = gw([Flaky("a")], store).complete(req())
    assert resp.text == '{"ok": true}' and resp.provider == "a"
    assert resp.cost_usd == pytest.approx((1000 * 1.0 + 500 * 2.0) / 1e6)
    calls = store.llm_calls(RUN)
    assert len(calls) == 1 and calls[0]["status"] == "ok" and calls[0]["agent"] == "analyst"
    assert json.loads(calls[0]["request_json"])[0]["content"].startswith("hello")
    assert types(store) == ["llm_call"]


def test_retry_then_recover_on_same_provider(store):
    a = Flaky("a", fail_times=1)
    resp = gw([a, Flaky("b")], store).complete(req())
    assert resp.provider == "a" and a.calls == 2 and resp.failed_attempts == 1
    assert "llm_retry" in types(store) and "llm_recovered" in types(store)


def test_failover_to_secondary(store):
    a, b = Flaky("a", fail_times=99), Flaky("b")
    resp = gw([a, b], store).complete(req())
    assert resp.provider == "b" and a.calls == 2
    assert [c["status"] for c in store.llm_calls(RUN)] == ["error", "error", "ok"]
    assert "provider_failover" in types(store)


def test_non_retryable_error_fails_over_immediately(store):
    a = Flaky("a", fail_times=99, retryable=False, status=401)
    resp = gw([a, Flaky("b")], store).complete(req())
    assert resp.provider == "b" and a.calls == 1


def test_all_providers_failed(store):
    with pytest.raises(AllProvidersFailed):
        gw([Flaky("a", 99), Flaky("b", 99)], store).complete(req())
    assert types(store)[-1] == "all_providers_failed"


def test_circuit_breaker_opens_and_skips(store):
    a, b = Flaky("a", fail_times=99), Flaky("b")
    g = gw([a, b], store, breaker_factory=lambda: CircuitBreaker(failure_threshold=2, cooldown_s=60))
    g.complete(req("one"))
    assert g.breakers["a"].state == "open" and "circuit_open" in types(store)
    g.complete(req("two"))
    assert a.calls == 2  # skipped on the second request
    assert "provider_skipped" in types(store)


def test_breaker_half_open_recovery():
    now = [0.0]
    br = CircuitBreaker(failure_threshold=1, cooldown_s=10, clock=lambda: now[0])
    assert br.record_failure() and not br.allow()
    now[0] = 11
    assert br.allow() and br.state == "half_open"
    assert br.record_failure() and br.state == "open"  # failed trial re-opens
    now[0] = 30
    assert br.allow()
    br.record_success()
    assert br.state == "closed"


def test_chaos_provider_down_triggers_failover(store):
    chaos = Chaos()
    chaos.set_provider_down("a")
    a, b = Flaky("a"), Flaky("b")
    resp = gw([a, b], store, chaos=chaos).complete(req())
    assert resp.provider == "b" and a.calls == 0
    assert "injected" in store.llm_calls(RUN)[0]["error"]


def test_chaos_corrupt_output_fires_once(store):
    chaos = Chaos()
    chaos.arm("corrupt_next", 1)
    g = gw([Flaky("a")], store, chaos=chaos)
    assert g.complete(req("x")).text == CORRUPTED_OUTPUT
    assert g.complete(req("y")).text == '{"ok": true}'
    assert "chaos_injected" in types(store)


def test_record_then_replay(store, tmp_path):
    cache = ResponseCache(tmp_path)
    a = Flaky("a")
    gw([a], store, cache=cache, cache_mode="read_write").complete(req())
    replay = gw([], store, cache=cache, cache_mode="replay_only").complete(req())
    assert replay.cache_hit and replay.text == '{"ok": true}' and replay.cost_usd == 0
    assert a.calls == 1
    with pytest.raises(ReplayMiss):
        gw([], store, cache=cache, cache_mode="replay_only").complete(req("never recorded"))


def test_cache_key_ignores_run_id():
    r1, r2 = req(), req()
    r2.run_id = "other"
    assert r1.cache_key() == r2.cache_key()


def test_budget_blocks_calls(store):
    g = gw([Flaky("a")], store, budgets=Budgets(max_llm_calls=2))
    g.complete(req("1"))
    g.complete(req("2"))
    with pytest.raises(BudgetExceeded):
        g.complete(req("3"))


def test_cost_budget(store):
    g = gw([Flaky("a")], store, budgets=Budgets(max_cost_usd=0.0015))
    g.complete(req("1"))
    with pytest.raises(BudgetExceeded, match="cost"):
        g.complete(req("2"))


def test_redaction_before_send(store):
    seen = []
    p = MockProvider("m", "m", lambda r: (seen.append(r.messages[0]["content"]), "{}")[1])
    gw([p], store).complete(req("key AKIAABCDEFGHIJKLMNOP card 4111111111111111"))
    assert "AKIA" not in seen[0] and "4111111111111111" in seen[0]
    assert "redaction" in types(store)


def test_mock_provider_serialises_dicts(store):
    p = MockProvider("m", "m", lambda r: {"agent": r.agent})
    assert json.loads(gw([p], store).complete(req()).text) == {"agent": "analyst"}


# ------------------------------------------------------------ real HTTP client against a mock transport


def http_provider(handler, json_mode=True):
    return OpenAICompatProvider(
        "gemini", "https://example.invalid/v1", "secret-key-123", "m", Pricing(0.3, 2.5),
        supports_json_mode=json_mode, transport=httpx.MockTransport(handler),
    )


def test_openai_compat_success():
    captured = {}

    def handler(request: httpx.Request):
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"a": 1}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        })

    c = http_provider(handler).complete(req())
    assert c.text == '{"a": 1}' and (c.prompt_tokens, c.completion_tokens) == (12, 5)
    assert captured["auth"] == "Bearer secret-key-123"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert str(captured["body"]["model"]) == "m"


def test_openai_compat_omits_json_mode_when_unsupported():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    c = http_provider(handler, json_mode=False).complete(req())
    assert "response_format" not in bodies[0] and c.completion_tokens >= 1


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (400, False), (401, False)])
def test_openai_compat_error_classification(status, retryable):
    p = http_provider(lambda r: httpx.Response(status, headers={"retry-after": "3"}, text="nope"))
    with pytest.raises(ProviderError) as ei:
        p.complete(req())
    assert ei.value.retryable is retryable and ei.value.status == status
    assert ei.value.retry_after == 3.0
    assert "secret-key-123" not in str(ei.value)


def test_openai_compat_network_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ProviderError) as ei:
        http_provider(handler).complete(req())
    assert ei.value.retryable


def test_openai_compat_bad_shape():
    with pytest.raises(ProviderError, match="unexpected response shape"):
        http_provider(lambda r: httpx.Response(200, json={"oops": 1})).complete(req())


def test_long_quota_wait_trips_breaker_and_fails_over(store):
    class Quota(Flaky):
        def complete(self, r):
            self.calls += 1
            raise ProviderError(self.name, "HTTP 429 daily quota", True, status=429, retry_after=38000)

    a, b = Quota("a"), Flaky("b")
    g = gw([a, b], store)
    assert g.complete(req("1")).provider == "b"
    assert a.calls == 1 and g.breakers["a"].state == "open"
    assert "quota_exhausted" in types(store)
    g.complete(req("2"))
    assert a.calls == 1  # skipped while its quota window is closed


def test_google_retry_delay_parsed():
    body = '[{"error": {"code": 429, "details": [{"retryDelay": "31s"}]}}]'
    p = http_provider(lambda r: httpx.Response(429, text=body))
    with pytest.raises(ProviderError) as ei:
        p.complete(req())
    assert ei.value.retry_after == 31.0


def test_providers_from_env_model_entries(monkeypatch):
    from forge.llm.providers import providers_from_env

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ps = providers_from_env(["gemini:gemini-x", "gemini", "groq", "nope"])
    assert [(p.name, p.model) for p in ps] == [("gemini:gemini-x", "gemini-x"), ("gemini", "gemini-3.5-flash")]
