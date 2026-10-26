"""Token + cost accounting (services/cost.py): price math and TokenMeter aggregation. No LLM/network —
the meter is fed fake LLMResults shaped like ChatOllama's (usage_metadata + model on the AIMessage)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

from config import OLLAMA_MODEL
from services.cost import MODEL_PRICES, TokenMeter, estimate_cost


def _result(model, in_tok, out_tok):
    """An LLMResult shaped like ChatOllama's: usage_metadata + model_name on the AIMessage."""
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def _result_no_usage():
    """An LLMResult with no usage reported (some responses omit usage_metadata)."""
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"))]])


# --- estimate_cost -----------------------------------------------------------------------------

def test_estimate_cost_price_math(monkeypatch):
    # Verify the $/1M-token multiplication itself, independent of what the real default model costs.
    monkeypatch.setitem(MODEL_PRICES, "test-priced-model", (1.0, 2.0))
    assert estimate_cost("test-priced-model", 1_000_000, 1_000_000) == 3.0


def test_estimate_cost_unknown_model_is_zero_no_raise():
    assert estimate_cost("gpt-imaginary-9", 1000, 1000) == 0.0


def test_estimate_cost_local_ollama_model_is_free():
    # Local inference has no metered API cost — the default model prices at $0 honestly, not "unknown".
    assert MODEL_PRICES[OLLAMA_MODEL] == (0.0, 0.0)
    assert estimate_cost(OLLAMA_MODEL, 5000, 5000) == 0.0


# --- TokenMeter --------------------------------------------------------------------------------

def test_meter_aggregates_multiple_calls(monkeypatch):
    monkeypatch.setitem(MODEL_PRICES, "test-priced-model", (1.0, 1.0))
    meter = TokenMeter()
    meter.on_llm_end(_result("test-priced-model", 100, 50))
    meter.on_llm_end(_result("test-priced-model", 200, 80))
    totals = meter.totals()
    assert totals["tokens_in"] == 300
    assert totals["tokens_out"] == 130
    assert totals["est_cost_usd"] > 0.0
    assert totals["unknown"] is False
    assert meter.calls == 2
    assert totals["by_model"]["test-priced-model"]["input_tokens"] == 300


def test_meter_local_model_tracks_tokens_at_zero_cost():
    meter = TokenMeter()
    meter.on_llm_end(_result(OLLAMA_MODEL, 100, 50))
    totals = meter.totals()
    assert totals["tokens_in"] == 100
    assert totals["tokens_out"] == 50
    assert totals["est_cost_usd"] == 0.0
    assert totals["unknown"] is False  # a $0 price is a real price, not an unknown model


def test_meter_flags_unknown_when_usage_missing():
    meter = TokenMeter()
    meter.on_llm_end(_result_no_usage())
    totals = meter.totals()
    assert totals["unknown"] is True
    assert totals["tokens_in"] == 0 and totals["tokens_out"] == 0
    assert totals["est_cost_usd"] == 0.0


def test_meter_mixed_known_and_missing():
    meter = TokenMeter()
    meter.on_llm_end(_result(OLLAMA_MODEL, 100, 50))
    meter.on_llm_end(_result_no_usage())
    totals = meter.totals()
    assert totals["tokens_in"] == 100  # the reported call still counts
    assert totals["unknown"] is True   # but the silent one is flagged
