"""Token + cost accounting for LLM-backed composition (unit-economics observability).

Closes the "know your $/active-user" half of the commercialization gate (``docs/COMMERCIALIZATION.md``):
per-node lesson caching already exists, but nothing measured token usage or cost. ``TokenMeter`` is a
LangChain callback attached at the top of a graph invocation, so it aggregates usage across *all*
nested LLM calls (creative + factual + synthesizer + exercise + grader) from a single chokepoint —
no per-agent edits.

Prices are coarse, prototype-grade estimates (USD per 1M tokens). They are constants here, not magic
numbers scattered through the code; update them when vendor pricing changes.
"""
import logging
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# (input $/1M, output $/1M). Verify/update against current vendor pricing.
#
# Local Ollama (config.OLLAMA_MODEL default tier, config.OLLAMA_MODEL_COMPLEX complex tier) is
# self-hosted, so it has no metered API cost and honestly prices at 0.0. That is a real price, not an
# "unknown model" placeholder: the meter still counts tokens and calls (see TokenMeter) so
# throughput/usage stays observable even when $/user is $0.
#
# Groq gpt-oss-120b (both tiers when GROQ_API_KEYS is set) is metered. Source: Groq's published
# on-demand pricing for openai/gpt-oss-120b, $0.15/1M input and $0.60/1M output (groq.com/pricing).
# The free tier bills $0 but is quota-capped, so these numbers are what the same usage would cost on
# the paid tier — that is the figure the unit-economics gate cares about.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "qwen2.5:14b": (0.0, 0.0),
    "qwen2.5:7b": (0.0, 0.0),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.10, 0.50),
}

_UNKNOWN_MODEL = "unknown"


def estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    """Estimate USD cost for a single model's token usage. Unknown model → 0.0 (logged), never raises."""
    price = MODEL_PRICES.get(model)
    if price is None:
        logger.warning("cost: no price for model %r — counting $0.00 (add it to MODEL_PRICES)", model)
        return 0.0
    in_price, out_price = price
    return (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price


def _model_name(result: LLMResult) -> str:
    """Best-effort model id from an LLMResult (varies by provider)."""
    output = result.llm_output or {}
    name = output.get("model_name") or output.get("model")
    if name:
        return str(name)
    # Fall back to the per-generation message metadata (ChatOllama stamps it there).
    for batch in result.generations:
        for gen in batch:
            meta = getattr(getattr(gen, "message", None), "response_metadata", None) or {}
            if meta.get("model_name") or meta.get("model"):
                return str(meta.get("model_name") or meta.get("model"))
    return _UNKNOWN_MODEL


def _usage(result: LLMResult) -> Optional[tuple[int, int]]:
    """Extract (input, output) token counts from an LLMResult, or None if the provider reported none."""
    # Preferred: the standardized usage_metadata on each chat generation's message.
    in_tok = out_tok = 0
    found = False
    for batch in result.generations:
        for gen in batch:
            meta = getattr(getattr(gen, "message", None), "usage_metadata", None)
            if meta:
                in_tok += int(meta.get("input_tokens", 0) or 0)
                out_tok += int(meta.get("output_tokens", 0) or 0)
                found = True
    if found:
        return in_tok, out_tok
    # Fallback: aggregate token_usage in llm_output (older provider shape).
    usage = (result.llm_output or {}).get("token_usage") or {}
    if usage:
        return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
    return None


class TokenMeter(BaseCallbackHandler):
    """Accumulates token usage + estimated cost across every LLM call in one graph invocation.

    Attach via ``graph.ainvoke(state, config={"callbacks": [meter]})``; LangChain propagates it to all
    nested runnables. A call that reports no usage (some responses simply omit ``usage_metadata``)
    sets ``unknown`` so a zero cost is never mistaken for a free real call. A priced $0 (local
    Ollama) is not ``unknown`` — it is a real price.
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.by_model: dict[str, dict[str, Any]] = {}
        self.unknown = False
        self.calls = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        self.calls += 1
        usage = _usage(response)
        if usage is None:
            self.unknown = True
            return
        in_tok, out_tok = usage
        model = _model_name(response)
        cost = estimate_cost(model, in_tok, out_tok)
        if model == _UNKNOWN_MODEL:
            self.unknown = True
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.cost += cost
        bucket = self.by_model.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "cost": 0.0})
        bucket["input_tokens"] += in_tok
        bucket["output_tokens"] += out_tok
        bucket["cost"] += cost

    def totals(self) -> dict[str, Any]:
        # Copy by_model so a stored result can't alias (and corrupt) the meter's internal state.
        return {
            "tokens_in": self.input_tokens,
            "tokens_out": self.output_tokens,
            "est_cost_usd": round(self.cost, 6),
            "by_model": {model: dict(b) for model, b in self.by_model.items()},
            "unknown": self.unknown,
        }
