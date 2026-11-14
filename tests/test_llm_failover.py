"""Two providers: failover (LLM_FALLBACK_PROVIDER) and an independent critic (CRITIC_PROVIDER). Offline:
models are built with dummy keys and never called."""
import pytest
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel

from services.agents import llm
from services.agents.stalls import FailoverMiddleware, StallCounter, StallRetryMiddleware


@pytest.fixture
def two_providers(monkeypatch):
    for k, v in {"GEMINI_API_KEY": "g-key", "GEMINI_FAST_MODEL": "gemini-test", "OPENAI_API_KEY": "o-key",
                 "OPENAI_FAST_MODEL": "openai-test", "LLM_PROVIDER": "gemini"}.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def test_no_failover_or_separate_critic_unless_asked(two_providers):
    assert llm.fallback_provider() is None and llm.critic_provider() == "gemini"
    assert llm.chat_providers("fast") == ["gemini"]


def test_failover_and_critic_providers(two_providers):
    two_providers.setenv("LLM_FALLBACK_PROVIDER", "openai")
    two_providers.setenv("CRITIC_PROVIDER", "openai")
    assert llm.fallback_provider() == "openai" and llm.critic_provider() == "openai"
    assert llm.chat_providers("fast") == ["gemini", "openai"]
    assert llm.fallback_provider("vision") is None  # no OPENAI_VISION_MODEL -> no vision failover


def test_same_or_unconfigured_provider_is_ignored(two_providers):
    two_providers.setenv("LLM_FALLBACK_PROVIDER", "gemini")  # same as LLM_PROVIDER
    assert llm.fallback_provider() is None
    two_providers.setenv("LLM_FALLBACK_PROVIDER", "openai")
    two_providers.setenv("CRITIC_PROVIDER", "openai")
    two_providers.delenv("OPENAI_API_KEY")
    assert llm.fallback_provider() is None and llm.critic_provider() == "gemini"
    two_providers.setenv("CRITIC_PROVIDER", "grok")
    with pytest.raises(ValueError):
        llm.critic_provider()


class Answer(BaseModel):
    ok: bool


class FakeChat:
    def __init__(self, prov, outcomes):
        self.prov, self.outcomes = prov, outcomes

    def with_structured_output(self, schema):
        return self

    def invoke(self, _messages):
        out = self.outcomes[self.prov].pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def test_structured_moves_to_the_fallback_provider(two_providers):
    two_providers.setenv("LLM_FALLBACK_PROVIDER", "openai")
    outcomes = {"gemini": [RuntimeError("429 RESOURCE_EXHAUSTED")] * 3, "openai": [Answer(ok=True)]}
    two_providers.setattr(llm, "chat_model", lambda kind, prov=None: FakeChat(prov, outcomes))
    assert llm.structured("fast", Answer, []) == Answer(ok=True)
    assert outcomes == {"gemini": [], "openai": []}


class Request:
    def __init__(self, model="primary"):
        self.model = model

    def override(self, model):
        return Request(model)


def test_failover_middleware_asks_the_backup_once():
    counter = StallCounter()
    mw = FailoverMiddleware("critic", "backup", counter)
    seen = []

    def handler(req):
        seen.append(req.model)
        if req.model == "primary":
            raise RuntimeError("429 RESOURCE_EXHAUSTED. quota")
        return f"answer from {req.model}"

    assert mw.wrap_model_call(Request(), handler) == "answer from backup"
    assert seen == ["primary", "backup"] and counter.failovers_by_agent == {"critic": 1}
    assert mw.wrap_model_call(Request("fine"), handler) == "answer from fine" and counter.failovers == 1


def test_failover_lets_interrupts_through():
    mw = FailoverMiddleware("orchestrator", "backup", StallCounter())

    def handler(req):
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        mw.wrap_model_call(Request(), handler)


def test_models_wire_failover_and_an_independent_critic(two_providers):
    from services.agents.orchestrator import _Models

    two_providers.setenv("LLM_FALLBACK_PROVIDER", "openai")
    two_providers.setenv("CRITIC_PROVIDER", "openai")
    m = _Models(StallCounter())
    assert type(m.main).__name__ == "ChatGoogleGenerativeAI" and type(m.main_backup).__name__ == "ChatOpenAI"
    assert type(m.critic).__name__ == "ChatOpenAI" and m.critic_backup is m.quick  # backed by the drafter's provider
    assert [type(x) for x in m.middleware("critic", m.critic_backup)] == [StallRetryMiddleware, FailoverMiddleware]


def test_models_without_a_second_provider_or_with_a_test_model(two_providers):
    from services.agents.orchestrator import _Models

    m = _Models(StallCounter())
    assert m.main_backup is None and m.critic is m.quick and m.critic_provider == "gemini"
    assert [type(x) for x in m.middleware("critic", m.critic_backup)] == [StallRetryMiddleware]
    fake = object()
    m = _Models(StallCounter(), fake)
    assert m.main is m.critic is fake and m.critic_provider is None


def test_xai_as_critic_and_failover(two_providers):
    from services.agents.orchestrator import _Models

    for k, v in {"XAI_API_KEY": "x-key", "XAI_FAST_MODEL": "grok-test",
                 "LLM_FALLBACK_PROVIDER": "xai", "CRITIC_PROVIDER": "xai"}.items():
        two_providers.setenv(k, v)
    m = _Models(StallCounter())
    assert type(m.critic).__name__ == type(m.main_backup).__name__ == "ChatXAI"
    assert m.critic.model_name == "grok-test" and type(m.main).__name__ == "ChatGoogleGenerativeAI"
