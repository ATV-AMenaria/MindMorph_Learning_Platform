"""Live smoke test: confirm with_structured_output actually works through whichever provider
config resolves — rotating Groq pool by default, local Ollama with no keys — using the app's own
code path (config.get_chat_model / agents).

NOT part of the pytest suite — hits a real provider, must be run manually:

    conda run -n mindmorph python scripts/live_smoke.py
"""
import sys
import os
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agents.orchestrator.orchestrator_agent import OrchestratorAgent
from agents.consensus.consensus_agent import ConsensusAgent


def timed(label, fn):
    print(f"\n--- {label} ---")
    t0 = time.time()
    try:
        result = fn()
    except Exception as e:
        dt = time.time() - t0
        print(f"FAILED after {dt:.2f}s: {type(e).__name__}: {e}")
        return None, dt, e
    dt = time.time() - t0
    print(f"OK in {dt:.2f}s")
    print(result)
    return result, dt, None


def main():
    import config
    from llm_providers import ProviderChain

    keys = len(config.groq_api_keys())
    if keys:
        print(
            f"Model path: Groq pool of {keys} keys -> Ollama fallback. Orchestrator uses default tier "
            f"({config.GROQ_MODEL}); Consensus uses complex tier ({config.GROQ_MODEL_COMPLEX}) "
            f"(json_schema structured output)"
        )
    else:
        print(
            f"Model path: no Groq keys — local Ollama only. Orchestrator uses default tier "
            f"({config.OLLAMA_MODEL}); Consensus uses complex tier ({config.OLLAMA_MODEL_COMPLEX})"
        )

    orch = OrchestratorAgent(push_to_langsmith=False)
    orch_result, orch_t, orch_err = timed(
        "Orchestrator.route_query (simple flat schema)",
        lambda: orch.route_query("I want to learn Python list comprehensions — create a roadmap for me"),
    )

    cons = ConsensusAgent()
    cons_result, cons_t, cons_err = timed(
        "ConsensusAgent.build_skill_graph (nested list-of-objects schema)",
        lambda: cons.build_skill_graph(
            "Learn Python list comprehensions",
            academic="Start with basic for-loops, then list comprehension syntax, then nested comprehensions.",
            market="List comprehensions are a common interview topic and appear in most Python codebases.",
            practical="Rewrite loops as comprehensions, then build a small data-filtering exercise.",
        ),
    )

    # Repeat the simple call once more to check reliability across repeated invocations.
    orch_result2, orch_t2, orch_err2 = timed(
        "Orchestrator.route_query (2nd call, reliability check)",
        lambda: orch.route_query("Explain what a Python decorator is"),
    )

    # One metered call so the model id the provider actually stamps can be checked against
    # services.cost.MODEL_PRICES — a mismatch there is what makes a paid call look free.
    from services.cost import MODEL_PRICES, TokenMeter

    meter = TokenMeter()
    try:
        config.llm.invoke("Say OK.", config={"callbacks": [meter]})
        totals = meter.totals()
        print(f"\n--- cost meter ---\n{totals}")
        for model in totals["by_model"]:
            print(f"price for {model!r} in MODEL_PRICES: {MODEL_PRICES.get(model, 'MISSING — would count $0')}")
    except Exception as e:
        print(f"\n--- cost meter --- FAILED: {type(e).__name__}: {e}")

    print("\n=== SUMMARY ===")
    print(f"Orchestrator call 1: {'OK' if orch_err is None else 'FAILED'} ({orch_t:.2f}s)")
    print(f"Consensus call:      {'OK' if cons_err is None else 'FAILED'} ({cons_t:.2f}s)")
    print(f"Orchestrator call 2: {'OK' if orch_err2 is None else 'FAILED'} ({orch_t2:.2f}s)")

    any_failed = any(e is not None for e in (orch_err, cons_err, orch_err2))
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
