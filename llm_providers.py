"""Provider chain: a pool of Groq API keys tried in order, with local Ollama as the last resort.

Groq's free tier meters per key (30 req/min, 1K req/day, 8K tok/min, 200K tok/day for
``openai/gpt-oss-120b``), so N keys multiply the effective quota ~Nx. This module owns the rotation
policy; ``config.get_chat_model`` owns which members go into the chain.

Three failure classes, deliberately treated differently:

- **Transient / per-key** — rate limit (429), token-per-minute cap (Groq returns 413 on some paths),
  auth rejection (401/403), or a server blip. The *next key* may well succeed, so advance and retry.
- **Transient / per-sample** — a structured-output parse failure (``OutputParserException``, which is
  what ``with_structured_output(..., method="json_schema")`` raises when the model's JSON doesn't
  validate). The completion arrived fine; only this *sample* was malformed. A different key runs the
  same model, so rotating is pointless — instead re-sample on the *same* member a bounded number of
  times. Before this existed, one bad Scout sample was misread as permanent and cost ~60s by
  abandoning the whole Groq pool for local Ollama.
- **Transient / per-endpoint** — a transport failure (``APIConnectionError``, its ``APITimeoutError``
  subclass, or a bare httpx transport error): the request never reached the API, so it carries no HTTP
  status. Every key points at the same host, so rotating is pointless — retry the *same* member a
  bounded number of times. The pool is built with ``max_retries=0``, so this layer is the only place a
  dropped socket gets a second chance.
- **Permanent / config** — above all 404 model-not-found. Every key will fail identically, so retrying
  across the pool just burns latency and hides the bug. This is the exact failure that silently
  degraded this project when Groq deprecated its Llama models, so it logs at ERROR and goes straight
  to the fallback.

Absence of an HTTP status does NOT imply permanence — that rule is what made both the parse failure
and the connection failure above masquerade as a deprecated model id. Permanence is the default only
for exception classes nothing here recognises.

Deliberately NOT a ``BaseChatModel`` subclass: the members already emit LangChain callbacks, so
wrapping them in a chat model would fire ``on_llm_end`` twice and double-count in ``services.cost``.
It is a thin facade over the surface the app actually uses — ``invoke`` / ``ainvoke`` /
``with_structured_output``.
"""
import logging
from typing import Any, Optional

import pydantic
from langchain_core.exceptions import OutputParserException

logger = logging.getLogger(__name__)

# HTTP statuses where a *different key* is worth trying. Everything else is treated as permanent.
ROTATE_STATUSES = frozenset({401, 403, 408, 413, 429, 500, 502, 503, 529})

# Parse/validation failures — retried on the SAME member, never used to widen the retryable set.
# LangChain's PydanticOutputParser already wraps pydantic's ValidationError into an
# OutputParserException, so the second entry is belt-and-braces for any caller that parses itself.
PARSE_EXCEPTIONS = (OutputParserException, pydantic.ValidationError)

# Extra attempts on the same member after a parse failure. One is enough: at temperature 0.1 a
# re-sample is genuinely different, and the fallback provider is still there if it isn't.
PARSE_RETRIES = 1


def _transport_exceptions() -> tuple:
    """Transport-level failure classes that can reach this layer, if their packages are installed.

    ``groq.APITimeoutError`` subclasses ``APIConnectionError``, so the base covers both. ``groq`` is
    imported lazily by ``config`` (a checkout with no keys need not have it), so a missing package
    must not break the import — it just means no SDK transport class exists to catch.
    """
    classes: list[type] = []
    for module_name, attr in (("groq", "APIConnectionError"), ("httpx", "TransportError")):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        cls = getattr(module, attr, None)
        if isinstance(cls, type):
            classes.append(cls)
    return tuple(classes)


TRANSPORT_EXCEPTIONS = _transport_exceptions()

# Extra attempts on the same member after a transport failure. Not rotated: all keys share one host,
# so a different key changes nothing. No sleep between attempts — the pool sets ``max_retries=0``, and
# a dropped socket is retried, not waited out; the fallback covers a genuine outage.
TRANSPORT_RETRIES = 2


def _is_parse_failure(exc: Exception) -> bool:
    return isinstance(exc, PARSE_EXCEPTIONS)


def _is_transport_failure(exc: Exception) -> bool:
    return bool(TRANSPORT_EXCEPTIONS) and isinstance(exc, TRANSPORT_EXCEPTIONS)


def _status_code(exc: Exception) -> Optional[int]:
    """Best-effort HTTP status from a Groq/openai-shaped SDK exception.

    Only 429/401/404 get named exception classes; TPM-limit 413s arrive as a generic
    ``APIStatusError``, so key off the status code, not the type.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


class ProviderChain:
    """Calls ``primary`` members in order, advancing on transient errors; then ``fallback``.

    ``primary`` is one member per API key (same vendor, same model). ``fallback`` is the zero-key
    local model, or ``None`` to re-raise instead.
    """

    def __init__(self, primary: list[Any], fallback: Any = None) -> None:
        self.primary = list(primary)
        self.fallback = fallback

    # -- rotation policy ------------------------------------------------------------------------

    def _handle(self, index: int, exc: Exception) -> bool:
        """Log the failure and report whether to try the next key."""
        code = _status_code(exc)
        position = f"key {index + 1}/{len(self.primary)}"
        if _is_parse_failure(exc):
            # Re-sampling already failed (see _invoke_member). Another key runs the same model, so
            # rotating would just repeat it — go to the fallback. Not a config error: WARNING, not
            # ERROR, so this stays visibly distinct from the 404 case below.
            logger.warning(
                "llm: %s returned unparseable structured output %d times (%s) — falling back",
                position, PARSE_RETRIES + 1, type(exc).__name__,
            )
            return False
        if _is_transport_failure(exc):
            # Retries already failed (see _invoke_member). The request never reached the API, and
            # every key targets the same host, so rotating would repeat the same socket failure N
            # times. Not a config error: WARNING, and no talk of model ids.
            logger.warning(
                "llm: %s could not reach the API after %d attempts (%s: %s) — network or endpoint "
                "problem, not a key problem; falling back",
                position, TRANSPORT_RETRIES + 1, type(exc).__name__, exc,
            )
            return False
        if code in ROTATE_STATUSES:
            logger.warning("llm: %s failed with HTTP %s (%s) — rotating to next key", position, code, type(exc).__name__)
            return True
        if code == 404:
            logger.error(
                "llm: %s failed with HTTP 404 — NOT rotating, falling back. The model id is wrong "
                "or deprecated; fix the config, the other keys will fail identically. (%s: %s)",
                position, type(exc).__name__, exc,
            )
            return False
        logger.error(
            "llm: %s failed with an unrecognised error (%s) — NOT rotating, falling back. "
            "This is neither a known transient status nor a transport failure; if it turns out to be "
            "retryable, classify it here rather than making every unknown error retryable. (%s: %s)",
            position, f"HTTP {code}" if code is not None else "no HTTP status",
            type(exc).__name__, exc,
        )
        return False

    def _log_resample(self, index: int, exc: Exception) -> None:
        logger.warning(
            "llm: key %d/%d returned unparseable structured output (%s) — re-sampling the same key",
            index + 1, len(self.primary), type(exc).__name__,
        )

    def _retry_same_member(self, index: int, exc: Exception, attempt: int) -> bool:
        """Should this member be tried again for ``exc``? Logs the reason when it should."""
        if _is_parse_failure(exc) and attempt < PARSE_RETRIES:
            self._log_resample(index, exc)
            return True
        if _is_transport_failure(exc) and attempt < TRANSPORT_RETRIES:
            logger.warning(
                "llm: key %d/%d could not reach the API (%s) — retrying the same key",
                index + 1, len(self.primary), type(exc).__name__,
            )
            return True
        return False

    def _invoke_member(self, index: int, args, kwargs):
        """One member, with bounded same-member retries on a parse or transport failure.

        No backoff between attempts: neither failure carries server-side rate state to wait out, and a
        sleep would add latency to exactly the path these retries exist to keep fast.
        """
        for attempt in range(max(PARSE_RETRIES, TRANSPORT_RETRIES) + 1):
            try:
                return self.primary[index].invoke(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - re-raised unless same-member retry applies
                if not self._retry_same_member(index, exc, attempt):
                    raise

    async def _ainvoke_member(self, index: int, args, kwargs):
        """Async twin of ``_invoke_member``."""
        for attempt in range(max(PARSE_RETRIES, TRANSPORT_RETRIES) + 1):
            try:
                return await self.primary[index].ainvoke(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - re-raised unless same-member retry applies
                if not self._retry_same_member(index, exc, attempt):
                    raise

    def _fallback_or_raise(self, exc: Optional[Exception]):
        if self.fallback is None:
            raise exc if exc is not None else RuntimeError("llm: no provider available")
        return self.fallback

    # -- delegated surface ----------------------------------------------------------------------

    def invoke(self, *args, **kwargs):
        last: Optional[Exception] = None
        for index in range(len(self.primary)):
            try:
                return self._invoke_member(index, args, kwargs)
            except Exception as exc:  # noqa: BLE001 - classified by status code below
                last = exc
                if not self._handle(index, exc):
                    break
        else:
            if self.primary:
                logger.warning("llm: all %d keys exhausted — falling back", len(self.primary))
        return self._fallback_or_raise(last).invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        last: Optional[Exception] = None
        for index in range(len(self.primary)):
            try:
                return await self._ainvoke_member(index, args, kwargs)
            except Exception as exc:  # noqa: BLE001 - classified by status code below
                last = exc
                if not self._handle(index, exc):
                    break
        else:
            if self.primary:
                logger.warning("llm: all %d keys exhausted — falling back", len(self.primary))
        return await self._fallback_or_raise(last).ainvoke(*args, **kwargs)

    async def astream(self, *args, **kwargs):
        """Stream tokens, rotating only on a failure that happens *before* the first token.

        Once tokens have reached the caller the reply is half-delivered; restarting on another key
        would duplicate text, so a mid-stream error propagates.

        No parse-retry here: only the tutor streams, and it streams plain text — nothing streamed is
        a structured-output model, so there is no parse failure to re-sample. Transport retries *do*
        apply: ``started`` is still False, so re-entering the stream cannot duplicate text.
        """
        last: Optional[Exception] = None
        for index, member in enumerate(self.primary):
            started = False
            failure: Optional[Exception] = None
            for attempt in range(TRANSPORT_RETRIES + 1):
                try:
                    async for chunk in member.astream(*args, **kwargs):
                        started = True
                        yield chunk
                    return
                except Exception as exc:  # noqa: BLE001 - classified by status code below
                    if started:
                        raise
                    failure = exc
                    if not self._retry_same_member(index, exc, attempt):
                        break
            last = failure
            if not self._handle(index, failure):
                break
        else:
            if self.primary:
                logger.warning("llm: all %d keys exhausted — falling back", len(self.primary))
        async for chunk in self._fallback_or_raise(last).astream(*args, **kwargs):
            yield chunk

    def with_structured_output(self, *args, **kwargs) -> "ProviderChain":
        """Map structured output over every member so the fallback keeps the schema too."""
        return ProviderChain(
            [m.with_structured_output(*args, **kwargs) for m in self.primary],
            self.fallback.with_structured_output(*args, **kwargs) if self.fallback is not None else None,
        )
