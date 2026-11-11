"""Test-wide setup. Set before any test imports `config`."""
import os

# Default the §6.3 content tail OFF for the suite so building a content graph never fires the real
# example/visual LLM agents. test_content_tail.py opts back in (monkeypatch) to exercise the tail.
os.environ.setdefault("MINDMORPH_RICH_CONTENT", "0")
# Network/model quality checks opt in explicitly in their own isolated runner.
os.environ.setdefault("MINDMORPH_WEB_RESEARCH", "0")
os.environ.setdefault("MINDMORPH_RERANK", "0")
os.environ.setdefault("MINDMORPH_NEO4J_MIRROR", "0")
# Knowledge-tracing tests opt in explicitly; ordinary flow tests retain their contract.
os.environ.setdefault("MINDMORPH_BKT", "0")
os.environ.setdefault("MM_WORKER_LOCK", "tests/fixtures/disabled-worker.lock.json")


import pytest


@pytest.fixture(autouse=True)
def isolated_rate_limits(monkeypatch, request):
    """Keep flow tests independent; authentication/rate-limit tests exercise limits."""
    from api.limiter import limiter
    limiter.reset()
    monkeypatch.setattr(limiter, "enabled", request.module.__name__.endswith("test_auth_and_rate_limiting"))
    yield
    limiter.reset()
