"""Wall-clock span timing for the LLM-backed stages (latency half of Gate-1 observability).

`services/cost.py` answers "what did this cost"; nothing answered "where did the time go" — every
performance question so far needed a throwaway monkeypatch harness. Same idiom as the cost meter:
record raw at one chokepoint per stage, read the aggregate afterwards.

Two surfaces, both fed by one `span()`:
  * a structured log line (`timing: <stage> <ms> ms`) — always emitted, works even with no collector;
  * an optional in-context dict collected by `collect()` and attached to the session payload.

The collector is a ContextVar holding a *mutable dict*, because the graph stages that matter most
(orchestrator / scout / specialists / consensus / reviewer) run BEFORE a learner session exists, so
there is no `ls` in scope to `record_event` into. The caller opens `collect()` around the graph run
and attaches the spans to the session it builds from the result.

Bounded by construction: keys are a fixed set of stage names, and each caller opens a fresh collector.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_spans: ContextVar[Optional[dict]] = ContextVar("mindmorph_spans", default=None)


@contextmanager
def collect() -> Iterator[dict]:
    """Collect the spans recorded inside this block into a dict of ``{stage: {ms, start_ms}}``.

    The dict is mutated in place by `span()` — never rebound — so spans recorded in tasks/threads
    that inherited this context (LangGraph node execution) land in the same dict the caller holds.
    """
    sink: dict = {"_origin": perf_counter()}
    token = _spans.set(sink)
    try:
        yield sink
    finally:
        _spans.reset(token)


@contextmanager
def span(stage: str, **fields) -> Iterator[None]:
    """Time the block and record it. Always logs; also stores when inside a `collect()`.

    ``start_ms`` is the offset from the collector's first span, so concurrent stages (the specialist
    fan-out) can be seen to actually overlap instead of being guessed at from durations alone.
    """
    sink = _spans.get()
    t0 = perf_counter()
    try:
        yield
    finally:
        elapsed_ms = round((perf_counter() - t0) * 1000, 1)
        logger.info(
            "timing: %s %.1f ms%s",
            stage,
            elapsed_ms,
            "".join(f" {k}={v!r}" for k, v in fields.items()),
        )
        if sink is not None:
            origin = sink.get("_origin", t0)
            entry = {"ms": elapsed_ms, "start_ms": round((t0 - origin) * 1000, 1)}
            sink[stage] = {**entry, **fields} if fields else entry


def spans_payload(sink: dict) -> dict:
    """Strip the private origin marker so the collected spans can go straight into a payload."""
    return {k: v for k, v in sink.items() if not k.startswith("_")}
