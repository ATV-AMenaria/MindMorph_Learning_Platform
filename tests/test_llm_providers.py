"""Key-rotation policy (llm_providers.ProviderChain) + provider selection (config.get_chat_model).

Fully hermetic: every member is a stub, no key is ever read, nothing touches the network. The
rotation rules under test are the ones that matter operationally — a rate-limited key must not take
the whole app down, an exhausted pool must land on local Ollama rather than raise, and a 404
model-not-found must NOT be silently retried across the pool (that failure mode silently degraded
this project for months when Groq deprecated its Llama models).
"""
import asyncio
import logging
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import groq
import httpx
from langchain_core.exceptions import OutputParserException
from langchain_ollama import ChatOllama

import config
from llm_providers import PARSE_RETRIES, TRANSPORT_RETRIES, ProviderChain, _status_code


class _HttpError(Exception):
    """Stands in for a Groq/openai-shaped SDK error, which carries .status_code."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _StubModel:
    """Records its calls; either raises a fixed error or returns a fixed answer.

    ``fail_times`` caps how many of the leading calls raise, so a stub can fail once and then
    succeed — which is what a re-sampled parse failure looks like.
    """

    def __init__(self, name, error=None, answer=None, fail_times=None):
        self.name = name
        self.error = error
        self.answer = answer if answer is not None else f"answer-from-{name}"
        self.fail_times = fail_times
        self.calls = 0

    def invoke(self, prompt, **kwargs):
        self.calls += 1
        if self.error is not None and (self.fail_times is None or self.calls <= self.fail_times):
            raise self.error
        return self.answer

    async def ainvoke(self, prompt, **kwargs):
        return self.invoke(prompt, **kwargs)

    def with_structured_output(self, schema, **kwargs):
        return _StubModel(f"{self.name}-structured", self.error, {"schema": schema.__name__})


class _StubStream:
    """Yields chunks, optionally raising after `fail_after` of them have been delivered."""

    def __init__(self, name, chunks=None, fail_after=None, error=None, fail_times=None):
        self.name = name
        self.chunks = chunks if chunks is not None else [f"from-{name}"]
        self.fail_after = fail_after
        self.error = error
        self.fail_times = fail_times  # as in _StubModel: fail the leading N calls, then succeed
        self.calls = 0

    async def astream(self, prompt, **kwargs):
        self.calls += 1
        if self.error is not None and self.fail_times is not None and self.calls > self.fail_times:
            self.error = None
        for i, chunk in enumerate(self.chunks):
            if self.error is not None and self.fail_after == i:
                raise self.error
            yield chunk
        if self.error is not None and self.fail_after == len(self.chunks):
            raise self.error


def _collect(agen):
    async def _run():
        return [chunk async for chunk in agen]

    return asyncio.run(_run())


# --- rotation policy ---------------------------------------------------------------------------

def test_rate_limited_key_advances_to_next_key():
    k1 = _StubModel("key1", error=_HttpError(429))
    k2 = _StubModel("key2")
    k3 = _StubModel("key3")
    chain = ProviderChain([k1, k2, k3], fallback=_StubModel("ollama"))

    assert chain.invoke("hi") == "answer-from-key2"
    assert (k1.calls, k2.calls, k3.calls) == (1, 1, 0)  # stops at the first key that works


def test_tpm_413_also_rotates():
    # Groq returns 413 for token-per-minute caps on some paths — same class of failure as a 429.
    k1 = _StubModel("key1", error=_HttpError(413))
    k2 = _StubModel("key2")
    assert ProviderChain([k1, k2]).invoke("hi") == "answer-from-key2"


def test_auth_failure_rotates_to_next_key():
    k1 = _StubModel("key1", error=_HttpError(401))
    k2 = _StubModel("key2")
    assert ProviderChain([k1, k2]).invoke("hi") == "answer-from-key2"


def test_all_keys_exhausted_falls_through_to_ollama():
    keys = [_StubModel(f"key{i}", error=_HttpError(429)) for i in range(5)]
    ollama = _StubModel("ollama")
    chain = ProviderChain(keys, fallback=ollama)

    assert chain.invoke("hi") == "answer-from-ollama"
    assert all(k.calls == 1 for k in keys)  # every key was actually tried
    assert ollama.calls == 1


def test_model_not_found_is_permanent_not_rotated(caplog):
    k1 = _StubModel("key1", error=_HttpError(404))
    k2 = _StubModel("key2")
    ollama = _StubModel("ollama")
    chain = ProviderChain([k1, k2], fallback=ollama)

    with caplog.at_level(logging.ERROR, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-ollama"

    assert k1.calls == 1
    assert k2.calls == 0, "a 404 must not burn the rest of the key pool"
    assert ollama.calls == 1
    assert any(rec.levelno == logging.ERROR for rec in caplog.records), "404 must log loudly at ERROR"
    assert "404" in caplog.text


# --- parse failures ----------------------------------------------------------------------------
# A structured-output parse failure carries no HTTP status, so the status-code rule used to read it
# as permanent and abandon the whole Groq pool for local Ollama — ~60s of a 92.6s graph build. It is
# sampling variance: re-sample the SAME key (a different key runs the same model), then fall back.

def test_parse_failure_retries_the_same_key_and_can_succeed(caplog):
    k1 = _StubModel("key1", error=OutputParserException("bad json"), fail_times=PARSE_RETRIES)
    k2 = _StubModel("key2")
    chain = ProviderChain([k1, k2], fallback=_StubModel("ollama"))

    with caplog.at_level(logging.WARNING, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-key1"

    assert k1.calls == PARSE_RETRIES + 1, "the retry must land on the same key"
    assert k2.calls == 0, "a parse failure must not rotate — another key runs the same model"
    assert "re-sampling the same key" in caplog.text
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records), (
        "a retried parse failure is not a config error and must not log at ERROR"
    )


def test_parse_failure_that_exhausts_retries_falls_through_to_fallback(caplog):
    k1 = _StubModel("key1", error=OutputParserException("bad json"))  # fails every time
    k2 = _StubModel("key2")
    ollama = _StubModel("ollama")
    chain = ProviderChain([k1, k2], fallback=ollama)

    with caplog.at_level(logging.WARNING, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-ollama"

    assert k1.calls == PARSE_RETRIES + 1
    assert k2.calls == 0
    assert ollama.calls == 1
    assert "unparseable structured output" in caplog.text
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records)


def test_parse_failure_retries_on_the_async_path_too():
    k1 = _StubModel("key1", error=OutputParserException("bad json"), fail_times=PARSE_RETRIES)
    chain = ProviderChain([k1], fallback=_StubModel("ollama"))
    assert asyncio.run(chain.ainvoke("hi")) == "answer-from-key1"
    assert k1.calls == PARSE_RETRIES + 1


# --- transport failures ------------------------------------------------------------------------
# An APIConnectionError (and its APITimeoutError subclass) never reached the API, so it carries no
# HTTP status — the old status-code rule read that as "permanent" and told the reader to go hunt a
# deprecated model id. It is a socket/network blip: retry the SAME key (all keys share one host, and
# the pool is built with max_retries=0 so nothing below us retries), then fall back.

def _connection_error():
    """A real groq.APIConnectionError — constructing one touches no network."""
    return groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com/v1/x"))


def test_transport_failure_retries_the_same_key_and_can_succeed(caplog):
    k1 = _StubModel("key1", error=_connection_error(), fail_times=TRANSPORT_RETRIES)
    k2 = _StubModel("key2")
    chain = ProviderChain([k1, k2], fallback=_StubModel("ollama"))

    with caplog.at_level(logging.WARNING, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-key1"

    assert k1.calls == TRANSPORT_RETRIES + 1, "the retries must land on the same key"
    assert k2.calls == 0
    assert "retrying the same key" in caplog.text
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records), (
        "a connection blip is not a config error and must not log at ERROR"
    )
    assert "deprecated" not in caplog.text, "must not send the reader hunting a model id"


def test_transport_failure_does_not_take_the_permanent_branch(caplog):
    # The captured-live bug: one connection error logged ERROR, blamed a 404 model id, and dropped
    # straight to Ollama on the first attempt.
    k1 = _StubModel("key1", error=_connection_error())  # fails every time
    ollama = _StubModel("ollama")
    chain = ProviderChain([k1], fallback=ollama)

    with caplog.at_level(logging.DEBUG, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-ollama"

    assert k1.calls == TRANSPORT_RETRIES + 1, "the key must not be abandoned after a single blip"
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records)
    assert "404" not in caplog.text and "model id" not in caplog.text


def test_transport_failure_exhausted_falls_through_to_the_fallback(caplog):
    k1 = _StubModel("key1", error=_connection_error())
    k2 = _StubModel("key2")
    ollama = _StubModel("ollama")
    chain = ProviderChain([k1, k2], fallback=ollama)

    with caplog.at_level(logging.WARNING, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-ollama"

    assert k1.calls == TRANSPORT_RETRIES + 1
    assert k2.calls == 0, "every key shares one host — rotating repeats the same socket failure"
    assert ollama.calls == 1
    assert "network or endpoint problem" in caplog.text


def test_timeout_is_treated_as_a_transport_failure():
    # APITimeoutError subclasses APIConnectionError, so the base class covers it.
    err = groq.APITimeoutError(request=httpx.Request("POST", "https://api.groq.com/v1/x"))
    k1 = _StubModel("key1", error=err, fail_times=TRANSPORT_RETRIES)
    assert ProviderChain([k1], fallback=_StubModel("ollama")).invoke("hi") == "answer-from-key1"
    assert k1.calls == TRANSPORT_RETRIES + 1


def test_transport_failure_retries_on_the_async_path_too():
    k1 = _StubModel("key1", error=_connection_error(), fail_times=TRANSPORT_RETRIES)
    chain = ProviderChain([k1], fallback=_StubModel("ollama"))
    assert asyncio.run(chain.ainvoke("hi")) == "answer-from-key1"
    assert k1.calls == TRANSPORT_RETRIES + 1


def test_stream_retries_the_same_key_when_the_socket_drops_before_the_first_token():
    # Nothing has reached the learner yet, so re-entering the stream cannot duplicate text.
    k1 = _StubStream("key1", chunks=["he", "llo"], fail_after=0,
                     error=_connection_error(), fail_times=TRANSPORT_RETRIES)
    k2 = _StubStream("key2", chunks=["wrong"])
    chain = ProviderChain([k1, k2], fallback=_StubStream("ollama"))

    assert _collect(chain.astream("hi")) == ["he", "llo"]
    assert k1.calls == TRANSPORT_RETRIES + 1
    assert k2.calls == 0


def test_non_parse_exception_without_status_is_still_permanent(caplog):
    # The retry must NOT widen into "any unknown exception gets a second shot". A plain ValueError
    # is the sharp case: OutputParserException subclasses it, so this proves the check keys on the
    # specific class rather than the base.
    k1 = _StubModel("key1", error=ValueError("something else"))
    k2 = _StubModel("key2")
    ollama = _StubModel("ollama")
    chain = ProviderChain([k1, k2], fallback=ollama)

    with caplog.at_level(logging.ERROR, logger="llm_providers"):
        assert chain.invoke("hi") == "answer-from-ollama"
    assert k1.calls == 1, "one shot only — no re-sample for a non-parse failure"
    assert k2.calls == 0
    # Permanent, but with no status: the log must say that, not blame a model id.
    assert "no HTTP status" in caplog.text
    assert "404" not in caplog.text and "deprecated" not in caplog.text


def test_exhausted_with_no_fallback_reraises_last_error():
    chain = ProviderChain([_StubModel("key1", error=_HttpError(429))], fallback=None)
    with pytest.raises(_HttpError):
        chain.invoke("hi")


def test_async_path_rotates_the_same_way():
    k1 = _StubModel("key1", error=_HttpError(429))
    k2 = _StubModel("key2")
    chain = ProviderChain([k1, k2], fallback=_StubModel("ollama"))
    assert asyncio.run(chain.ainvoke("hi")) == "answer-from-key2"


def test_stream_rotates_when_the_failure_precedes_the_first_token():
    k1 = _StubStream("key1", fail_after=0, error=_HttpError(429))
    k2 = _StubStream("key2", chunks=["he", "llo"])
    chain = ProviderChain([k1, k2], fallback=_StubStream("ollama"))
    assert _collect(chain.astream("hi")) == ["he", "llo"]


def test_stream_does_not_restart_mid_reply():
    # Half a reply is already on the learner's screen; rotating would duplicate it.
    k1 = _StubStream("key1", chunks=["par", "tial"], fail_after=1, error=_HttpError(429))
    k2 = _StubStream("key2", chunks=["fresh"])
    chain = ProviderChain([k1, k2])
    with pytest.raises(_HttpError):
        _collect(chain.astream("hi"))
    assert k2.calls == 0


def test_structured_output_maps_over_fallback_too():
    class Schema:
        pass

    chain = ProviderChain([_StubModel("key1", error=_HttpError(429))], fallback=_StubModel("ollama"))
    structured = chain.with_structured_output(Schema)

    assert isinstance(structured, ProviderChain)
    # The fallback keeps the schema — losing it there would silently degrade output shape.
    assert structured.invoke("hi") == {"schema": "Schema"}


def test_status_code_read_from_nested_response():
    class _Resp:
        status_code = 429

    class _Err(Exception):
        response = _Resp()

    assert _status_code(_Err()) == 429
    assert _status_code(ValueError("no status here")) is None


# --- provider selection ------------------------------------------------------------------------

def test_no_keys_yields_plain_ollama_even_when_provider_is_groq(monkeypatch):
    # The README's "no API key required" promise: an explicit provider=groq with an empty pool must
    # degrade to local Ollama, not raise.
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("MINDMORPH_LLM_PROVIDER", "groq")
    assert isinstance(config.get_chat_model("default"), ChatOllama)


def test_blank_keys_are_treated_as_no_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", " , ,")
    monkeypatch.setenv("MINDMORPH_LLM_PROVIDER", "groq")
    assert config.groq_api_keys() == []
    assert isinstance(config.get_chat_model("default"), ChatOllama)


def test_keys_present_builds_one_member_per_key_with_ollama_fallback(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "stub-a,stub-b,stub-c")
    monkeypatch.delenv("MINDMORPH_LLM_PROVIDER", raising=False)  # default flips to groq when keys exist
    monkeypatch.setenv("MINDMORPH_LLM_FALLBACK", "ollama")

    model = config.get_chat_model("complex")
    assert isinstance(model, ProviderChain)
    assert len(model.primary) == 3
    assert isinstance(model.fallback, ChatOllama)
    assert model.fallback.model == config.OLLAMA_MODEL_COMPLEX  # tier survives into the fallback
    assert all(m.model_name == config.GROQ_MODEL_COMPLEX for m in model.primary)


def test_explicit_ollama_provider_ignores_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "stub-a,stub-b")
    assert isinstance(config.get_chat_model("default", provider="ollama"), ChatOllama)


def test_unknown_provider_still_raises(monkeypatch):
    monkeypatch.setenv("MINDMORPH_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        config.get_chat_model("default")
