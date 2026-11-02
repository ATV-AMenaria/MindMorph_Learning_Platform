"""Operator-only, persisted upper bounds for live acceptance model calls.

All retries/fallbacks pass through the LangChain model boundary in this isolated
evaluation process. Never imported by normal application requests.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from threading import BoundedSemaphore
from types import SimpleNamespace
import asyncio
import time

from langchain_core.language_models.chat_models import BaseChatModel


PRICES = {"openai/gpt-oss-120b": (.15, .60), "openai/gpt-oss-20b": (.075, .30)}
PRICE_SOURCES = ["https://console.groq.com/docs/model/openai/gpt-oss-120b",
                 "https://console.groq.com/docs/model/openai/gpt-oss-20b"]


class EvaluationStopped(BaseException):
    """Bypass application fallback/repair handlers when a shared ceiling is hit."""


class LiveBudget:
    def __init__(self, path, *, calls=500, tokens=2_000_000, dollars=5, max_output=8192, paced=False):
        self.path = Path(path)
        self.lock = RLock()
        self.max_output = max_output
        self.paced = paced
        self.dispatch = BoundedSemaphore(1)
        self.routing_cursor = 0
        self.limits = {"calls": calls, "tokens": tokens, "usd": dollars}
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "calls": [], "limits": self.limits, "pricing_sources": PRICE_SOURCES, "pricing_checked": "2026-10-30"}
        if self.data["limits"] != self.limits:
            raise ValueError("An existing evaluation ledger cannot silently change its limits")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def totals(self):
        rows = self.data["calls"]
        return {"calls": len(rows), "tokens_upper": sum(r["tokens_upper"] for r in rows),
                "observed_tokens": sum(r.get("observed_tokens", 0) for r in rows),
                "usd_upper": sum(r["usd_upper"] for r in rows),
                "observed_est_usd": sum(r.get("observed_est_usd", 0) for r in rows),
                "unobserved_calls": sum("observed_tokens" not in r for r in rows)}

    def allow_retry_after_cooldown(self):
        """Explicit availability retry; never resets the accumulated budget."""
        with self.lock:
            if self.data['calls']:
                started = datetime.fromisoformat(self.data['calls'][-1]['started_at'])
                if (datetime.now(timezone.utc) - started).total_seconds() < 60:
                    raise EvaluationStopped('Wait at least60 seconds before rechecking throttled availability')
            self.data['throttle_restart_index'] = len(self.data['calls'])
            self._save()

    def retry_delay(self):
        with self.lock:
            rows = self.data['calls']
            if not self.paced or not rows or rows[-1].get('http_status') != 429:
                return 0
            row = rows[-1]
            end = datetime.fromisoformat(row.get('ended_at', row['started_at']))
            return min(60, max(0, row.get('retry_after_seconds', 10) + 1 - (datetime.now(timezone.utc) - end).total_seconds()))

    def reserve(self, model, messages, kwargs):
        vendor = type(model).__module__
        name = getattr(model, "model_name", None) or getattr(model, "model", "unknown")
        if "groq" in vendor:
            if name not in PRICES:
                raise EvaluationStopped("Live evaluation requires verified pricing for the configured Groq model")
            price = PRICES[name]
            kwargs["max_tokens"] = self.max_output
            kwargs["timeout"] = 60
        elif "ollama" in vendor:
            price = (0, 0)
            model.num_predict = self.max_output
            import httpx
            for name_attr in ("_client", "_async_client"):
                client = getattr(getattr(model, name_attr, None), "_client", None)
                if client is not None:
                    client.timeout = httpx.Timeout(60)
        else:
            raise EvaluationStopped("Only the configured Groq/Ollama chain is allowed")
        # UTF-8 bytes conservatively bound text tokens. Include serialized schemas
        # and an extra margin for provider message framing/structured-output wrappers.
        payload = [m.model_dump() if hasattr(m, "model_dump") else m for m in messages]
        input_upper = len(json.dumps([payload, kwargs], default=str, ensure_ascii=False).encode()) + 4096
        token_upper = input_upper + self.max_output
        usd_upper = (input_upper * price[0] + self.max_output * price[1]) / 1_000_000
        with self.lock:
            recent = self.data["calls"][self.data.get('throttle_restart_index', 0):]
            if len(recent) >= 3 and all(r.get("http_status") == 429 for r in recent[-3:]):
                raise EvaluationStopped("Repeated provider throttling; remaining live checks stay open")
            totals = self.totals()
            if (totals["calls"] >= self.limits["calls"] or totals["tokens_upper"] + token_upper > self.limits["tokens"]
                    or totals["usd_upper"] + usd_upper > self.limits["usd"]):
                raise EvaluationStopped("The shared live evaluation budget would be exceeded")
            index = len(self.data["calls"])
            self.data["calls"].append({"model": name, "started_at": datetime.now(timezone.utc).isoformat(),
                "tokens_upper": token_upper, "usd_upper": usd_upper, "state": "reserved", "price": price})
            self._save()
            return index

    def complete(self, index, result=None, error=None):
        with self.lock:
            row = self.data["calls"][index]
            row['ended_at'] = datetime.now(timezone.utc).isoformat()
            row["state"] = "failed" if error else "completed"
            if error:
                row["error_type"] = type(error).__name__
                status = getattr(error, "status_code", None)
                if isinstance(status, int): row["http_status"] = status
                if status == 429:
                    import re
                    body = getattr(error, 'body', None) or {}
                    message = json.dumps(body, default=str)
                    match = re.search(r'(tokens per minute|tokens per day|requests per minute|requests per day)', message, re.I)
                    if match: row['rate_limit_kind'] = match.group(1).lower()
                    retry = getattr(getattr(error, 'response', None), 'headers', {}).get('retry-after')
                    try: row['retry_after_seconds'] = min(86400, max(0, float(retry)))
                    except (ValueError, TypeError): pass
            usage = None
            if result:
                generations = getattr(result, "generations", [])
                metas = [getattr(getattr(g, "message", None), "usage_metadata", None) for g in generations]
                if metas and all(m is not None for m in metas):
                    usage = (sum(m.get("input_tokens", 0) for m in metas), sum(m.get("output_tokens", 0) for m in metas))
                else:
                    meta = (getattr(result, "llm_output", None) or {}).get("token_usage")
                    if meta: usage = (meta.get("prompt_tokens", 0), meta.get("completion_tokens", 0))
            if usage:
                incoming, outgoing = usage
                observed = incoming + outgoing
                if observed > row["tokens_upper"]:
                    raise EvaluationStopped("Provider token usage exceeded its reserved bound")
                cost = (incoming * row["price"][0] + outgoing * row["price"][1]) / 1_000_000
                row.update(tokens_upper=observed, observed_tokens=observed, usd_upper=cost, observed_est_usd=cost)
            self._save()

    @contextmanager
    def install(self):
        original = BaseChatModel._generate_with_cache
        original_async = BaseChatModel._agenerate_with_cache
        original_stream, original_astream = BaseChatModel.stream, BaseChatModel.astream
        budget = self
        def sync(model, messages, *args, **kwargs):
            if budget.paced: budget.dispatch.acquire()
            try:
                time.sleep(budget.retry_delay())
                index = budget.reserve(model, messages, kwargs)
                try:
                    result = original(model, messages, *args, **kwargs)
                    budget.complete(index, result)
                    return result
                except BaseException as error:
                    budget.complete(index, error=error)
                    raise
            finally:
                if budget.paced: budget.dispatch.release()
        async def asynchronous(model, messages, *args, **kwargs):
            if budget.paced: await asyncio.to_thread(budget.dispatch.acquire)
            try:
                await asyncio.sleep(budget.retry_delay())
                index = budget.reserve(model, messages, kwargs)
                try:
                    result = await original_async(model, messages, *args, **kwargs)
                    budget.complete(index, result)
                    return result
                except BaseException as error:
                    budget.complete(index, error=error)
                    raise
            finally:
                if budget.paced: budget.dispatch.release()
        def stream(model, value, *args, **kwargs):
            index = budget.reserve(model, model._convert_input(value).to_messages(), kwargs)
            combined = None
            try:
                for chunk in original_stream(model, value, *args, **kwargs):
                    combined = chunk if combined is None else combined + chunk
                    yield chunk
                budget.complete(index, SimpleNamespace(generations=[SimpleNamespace(message=combined)]))
            except BaseException as error:
                budget.complete(index, error=error)
                raise
        async def astream(model, value, *args, **kwargs):
            index = budget.reserve(model, model._convert_input(value).to_messages(), kwargs)
            combined = None
            try:
                async for chunk in original_astream(model, value, *args, **kwargs):
                    combined = chunk if combined is None else combined + chunk
                    yield chunk
                budget.complete(index, SimpleNamespace(generations=[SimpleNamespace(message=combined)]))
            except BaseException as error:
                budget.complete(index, error=error)
                raise
        BaseChatModel._generate_with_cache = sync
        BaseChatModel._agenerate_with_cache = asynchronous
        BaseChatModel.stream, BaseChatModel.astream = stream, astream
        from llm_providers import ProviderChain
        old_invoke, old_ainvoke, old_astream = ProviderChain.invoke, ProviderChain.ainvoke, ProviderChain.astream
        def rotate(chain):
            if not budget.paced or not chain.primary: return chain
            with budget.lock:
                index = budget.routing_cursor % len(chain.primary)
                budget.routing_cursor += 1
            return ProviderChain(chain.primary[index:] + chain.primary[:index], chain.fallback)
        def routed_invoke(chain, *args, **kwargs): return old_invoke(rotate(chain), *args, **kwargs)
        async def routed_ainvoke(chain, *args, **kwargs): return await old_ainvoke(rotate(chain), *args, **kwargs)
        async def routed_astream(chain, *args, **kwargs):
            async for chunk in old_astream(rotate(chain), *args, **kwargs): yield chunk
        ProviderChain.invoke, ProviderChain.ainvoke, ProviderChain.astream = routed_invoke, routed_ainvoke, routed_astream
        try:
            yield self
        finally:
            BaseChatModel._generate_with_cache = original
            BaseChatModel._agenerate_with_cache = original_async
            BaseChatModel.stream, BaseChatModel.astream = original_stream, original_astream
            ProviderChain.invoke, ProviderChain.ainvoke, ProviderChain.astream = old_invoke, old_ainvoke, old_astream
            self._save()
