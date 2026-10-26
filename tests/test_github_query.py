"""GitHub `q` normalization: prose questions must become bounded keyword queries.

The Scout agent emits natural-language PRACTICAL questions, and the Practical node forwarded them
verbatim to GitHub's repository search. GitHub rejects an over-long `q`
(`Search.q (invalid): The search is longer than 256 characters`), the ToolException was swallowed,
and the Practical agent silently degraded to `grounded=False` — seven times in logs/mindmorph.log.
These tests pin the fix: extraction is deterministic (no LLM on this latency-sensitive path), the
result is always bounded, and the no-token short-circuit still degrades quietly-but-loudly.
"""
import logging

import pytest

import graph.learning_plan_graph as glp
from tools.github_mcp_client import (
    MAX_QUERY_CHARS,
    MAX_QUERY_TERMS,
    MCPClientInitialization,
    to_search_query,
)

# Verbatim from logs/mindmorph.log line 11793 — note the U+2011 non-breaking hyphens the Scout
# writes ("open‑source", "real‑world"), which a naive tokenizer mangles.
REAL_OVERFLOW_QUERY = (
    "What common open‑source Rust projects and systems‑programming implementations are "
    "found on GitHub, and which libraries, frameworks, and design patterns are frequently used in "
    "real‑world Rust system code?"
)


class _CapturingTool:
    """Records the args it was invoked with so the test can assert on the outgoing `q`."""

    name = "search_repositories"

    def __init__(self):
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return "REPOS"


def test_extracts_topic_terms_from_a_realistic_scout_question():
    q = to_search_query(REAL_OVERFLOW_QUERY)

    terms = q.split()
    assert len(terms) <= MAX_QUERY_TERMS
    # The topic survives; the question scaffolding does not.
    assert "Rust" in terms
    assert "systems-programming" in terms  # U+2011 folded to ASCII, kept as one term
    assert not {"What", "what", "common", "found", "GitHub"} & set(terms)


def test_result_is_always_bounded():
    assert len(to_search_query("python " * 500)) <= MAX_QUERY_CHARS
    assert len(to_search_query(REAL_OVERFLOW_QUERY)) <= MAX_QUERY_CHARS


def test_falls_back_to_truncation_when_everything_is_a_stopword():
    # An empty `q` is rejected by GitHub too, so stripping everything must not yield "" — and the
    # fallback still has to respect the bound, so use an input well past MAX_QUERY_CHARS.
    all_stopwords = "what are the most common projects " * 12
    assert len(all_stopwords) > MAX_QUERY_CHARS

    q = to_search_query(all_stopwords)

    assert 0 < len(q) <= MAX_QUERY_CHARS
    assert not q.endswith(" ")
    assert all_stopwords.split()[: len(q.split())] == q.split()  # cut on a word boundary


def test_keeps_technology_tokens_intact():
    assert to_search_query("Which C++ and node.js 3.11 tools?").split() == ["C++", "node.js", "3.11", "tools"]


@pytest.mark.asyncio
async def test_overlong_query_is_normalized_before_reaching_github():
    client = MCPClientInitialization()
    tool = _CapturingTool()
    client.tools = [tool]

    result = await client.search_github_repositories(REAL_OVERFLOW_QUERY)

    assert result == "REPOS"
    sent = tool.calls[0]["query"]
    assert sent == to_search_query(REAL_OVERFLOW_QUERY)
    assert len(sent) <= MAX_QUERY_CHARS
    assert tool.calls[0]["perPage"] == 5


@pytest.mark.asyncio
async def test_missing_token_short_circuits_and_says_why(monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_PERSONAL_TOKEN", "")

    with caplog.at_level(logging.WARNING, logger="graph.learning_plan_graph"):
        assert await glp._fetch_github_repos("anything") is None

    assert "GITHUB_PERSONAL_TOKEN" in caplog.text
