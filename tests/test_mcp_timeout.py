"""MCP network calls are timeout-bounded.

A flapping/half-open MCP (SSE) connection used to block an `await` indefinitely,
wedging the LangGraph fan-out (the fan-in node never fired). These tests prove
the `wait_for` guard fires: a slow tool degrades to the method's "no data" value
within the timeout instead of hanging, and any reused client state is reset so a
later request rebuilds cleanly. A fast tool still returns results (no regression).
"""
import asyncio
import json

import pytest

from tools.github_mcp_client import MCPClientInitialization
from tools.job_scrapper_tool import JobScraperService

# Comfortably above the test timeout — never actually elapses (wait_for cancels it).
SLOW_SECONDS = 30


@pytest.fixture(autouse=True)
def _tiny_timeout(monkeypatch):
    """Make the MCP timeout tiny so a 'slow' tool trips it almost instantly."""
    monkeypatch.setenv("MINDMORPH_MCP_TIMEOUT", "0.05")


class _FakeTool:
    def __init__(self, name, *, result=None, sleep=0.0):
        self.name = name
        self._result = result
        self._sleep = sleep

    async def ainvoke(self, _payload):
        if self._sleep:
            await asyncio.sleep(self._sleep)
        return self._result


class _FakeClient:
    """Stands in for MultiServerMCPClient: get_tools() may be fast or slow."""

    def __init__(self, tools, *, get_tools_sleep=0.0):
        self._tools = tools
        self._sleep = get_tools_sleep

    async def get_tools(self):
        if self._sleep:
            await asyncio.sleep(self._sleep)
        return self._tools


# --- GitHub MCP client ---------------------------------------------------------

async def test_github_search_times_out_and_discards_client():
    slow_tool = _FakeTool("search_repositories", sleep=SLOW_SECONDS)
    client = MCPClientInitialization()
    client.client = _FakeClient([slow_tool])

    result = await asyncio.wait_for(
        client.search_github_repositories("anything"), timeout=5
    )

    assert result is None
    assert client.client is None  # half-open client dropped for the next call


async def test_github_get_tools_timeout_returns_none():
    client = MCPClientInitialization()
    client.client = _FakeClient([], get_tools_sleep=SLOW_SECONDS)

    result = await asyncio.wait_for(
        client.search_github_repositories("anything"), timeout=5
    )

    assert result is None
    assert client.client is None


async def test_github_search_happy_path_returns_result():
    tool = _FakeTool("search_repositories", result={"items": [{"name": "repo"}]})
    client = MCPClientInitialization()
    client.client = _FakeClient([tool])

    result = await client.search_github_repositories("langchain")

    assert result == {"items": [{"name": "repo"}]}
    assert client.client is not None  # no reset on success


# --- JobScraper (Apify) MCP client --------------------------------------------

async def test_job_search_times_out_and_resets_state():
    slow_tool = _FakeTool(JobScraperService.SEARCH_TOOL_NAME, sleep=SLOW_SECONDS)
    svc = JobScraperService()
    svc.client = object()           # reused singleton client
    svc.tools = [slow_tool]

    result = await asyncio.wait_for(svc.search_jobs("python dev"), timeout=5)

    assert result is None
    assert svc.client is None       # reset so next initialize() rebuilds clean
    assert svc.tools == []


async def test_job_fetch_times_out_and_resets_state():
    slow_tool = _FakeTool(JobScraperService.OUTPUT_TOOL_NAME, sleep=SLOW_SECONDS)
    svc = JobScraperService()
    svc.client = object()
    svc.tools = [slow_tool]

    result = await asyncio.wait_for(svc.fetch_job_results("ds-123"), timeout=5)

    assert result == []
    assert svc.client is None
    assert svc.tools == []


async def test_job_search_happy_path_returns_dataset_id():
    run_summary = json.dumps(
        {"storages": {"datasets": {"default": {"id": "ds-abc", "itemCount": 3}}}}
    )
    tool = _FakeTool(
        JobScraperService.SEARCH_TOOL_NAME,
        result=[{"type": "text", "text": run_summary}],
    )
    svc = JobScraperService()
    svc.tools = [tool]

    result = await svc.search_jobs("python dev")

    assert result == "ds-abc"


# --- JobScraper handshake reuse ------------------------------------------------
# The connect + get_tools handshake costs ~4.3s and was re-paid on every request even
# though the service is a per-process singleton. It is now cached — which only stays
# safe because every failure path clears it, so a wedged client can't survive.

async def test_initialize_reuses_cached_tools_without_rehandshaking():
    tool = _FakeTool(JobScraperService.SEARCH_TOOL_NAME)
    svc = JobScraperService()
    sentinel = object()
    svc.client = sentinel
    svc.tools = [tool]

    assert await svc.initialize() is sentinel  # no new MultiServerMCPClient built
    assert svc.tools == [tool]


async def test_job_search_error_clears_cached_tools():
    class _Boom(_FakeTool):
        async def ainvoke(self, _payload):
            raise RuntimeError("actor exploded")

    svc = JobScraperService()
    svc.client = object()
    svc.tools = [_Boom(JobScraperService.SEARCH_TOOL_NAME)]

    assert await svc.search_jobs("python dev") is None
    assert svc.tools == []  # next initialize() re-handshakes instead of reusing a wedged client


async def test_job_search_clears_cached_tools_when_the_tool_is_missing():
    """A handshake that cached a partial tool list must not wedge every later request."""
    svc = JobScraperService()
    svc.client = object()
    svc.tools = [_FakeTool("some-other-tool")]

    assert await svc.search_jobs("python dev") is None
    assert svc.tools == []
