"""Deterministic repair of an incoherent Consensus skill graph (dangling edges).

Reconstructs the real failing payload: the model emitted edges sourced from ``mlops_tools``, a node
id absent from the node list, which permanently locked ``production_ml_systems`` and
``model_deployment_and_monitoring`` and made ``is_session_complete`` unreachable.

Two lanes for one invariant: BUILD time (``prune_dangling_edges`` in consensus_node) keeps new graphs
clean; READ time (``prereqs_by_node``, which reuses the same function) keeps sessions persisted before
that landed finishable, without rewriting stored learner state. All hermetic — no LLM, no network.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import graph.learning_plan_graph as glp
from graph.learning_plan_graph import build_graph
from graph.skill_graph_adapt import prune_dangling_edges
from services.completion import (
    _warn_dangling,
    complete_node_ids,
    incomplete_prereq_labels,
    is_session_complete,
    locked_node_ids,
    prereqs_by_node,
)


def _incoherent_graph() -> dict:
    """The live failure: `mlops_tools` is referenced by edges but is not a node."""
    return {
        "summary": "ML engineering roadmap",
        "nodes": [
            {"id": "python_basics", "label": "Python Basics", "level": "foundational"},
            {"id": "ml_fundamentals", "label": "ML Fundamentals", "level": "intermediate"},
            {"id": "production_ml_systems", "label": "Production ML Systems", "level": "advanced"},
            {"id": "model_deployment_and_monitoring",
             "label": "Model Deployment and Monitoring", "level": "advanced"},
        ],
        "edges": [
            {"source": "python_basics", "target": "ml_fundamentals", "relation": "prerequisite"},
            {"source": "ml_fundamentals", "target": "production_ml_systems", "relation": "prerequisite"},
            # Hallucinated endpoint — source side is the damaging one (prereqs_by_node keys on target).
            {"source": "mlops_tools", "target": "production_ml_systems", "relation": "prerequisite"},
            {"source": "mlops_tools", "target": "model_deployment_and_monitoring",
             "relation": "prerequisite"},
            # Target side: silently ignored by completion, but still renders a phantom mermaid box.
            {"source": "ml_fundamentals", "target": "mlops_tools", "relation": "prerequisite"},
        ],
    }


def _all_mastered(graph: dict) -> dict:
    return {n["id"]: {"status": "mastered", "best_score": 100} for n in graph["nodes"]}


def _healthy_graph() -> dict:
    bad = _incoherent_graph()
    return bad | {"edges": [e for e in bad["edges"] if "mlops_tools" not in (e["source"], e["target"])]}


# --- Read-time guard: a session PERSISTED before the build-time prune landed -------------------
# Those sessions are stored whole (persistence/models.py) and rehydrated verbatim — nothing
# revalidates them — so the guard has to live where prerequisites are computed.

def test_persisted_broken_session_is_finishable_without_rewriting_it():
    """The exact regression: a stored graph with a phantom SOURCE must still be completable."""
    bad = _incoherent_graph()
    stored_edges = [dict(e) for e in bad["edges"]]
    state = _all_mastered(bad)

    assert locked_node_ids(bad, state) == set()
    assert complete_node_ids(bad, state) == {n["id"] for n in bad["nodes"]}
    assert is_session_complete(bad, state) is True
    assert bad["edges"] == stored_edges  # stored session untouched — no migration, no rewrite


def test_read_time_guard_only_ignores_the_phantom_prerequisite():
    """Real prerequisites still gate; only the unmasterable phantom is dropped."""
    bad = _incoherent_graph()
    assert prereqs_by_node(bad) == {
        "python_basics": set(),
        "ml_fundamentals": {"python_basics"},
        "production_ml_systems": {"ml_fundamentals"},   # phantom source gone, real one kept
        "model_deployment_and_monitoring": set(),        # only prereq was the phantom -> a root
    }

    fresh = {n["id"]: {"status": "available", "best_score": 0} for n in bad["nodes"]}
    assert "production_ml_systems" in locked_node_ids(bad, fresh)
    assert "model_deployment_and_monitoring" not in locked_node_ids(bad, fresh)
    assert is_session_complete(bad, fresh) is False

    # is_session_complete fires only once the REAL prerequisites are mastered.
    partial = fresh | {"python_basics": {"status": "mastered", "best_score": 100}}
    assert is_session_complete(bad, partial) is False


def test_phantom_id_never_leaks_into_the_lock_message():
    """The raw slug used to surface as 'Requires: …, mlops_tools, …' in the UI/accessible name."""
    bad = _incoherent_graph()
    fresh = {n["id"]: {"status": "available", "best_score": 0} for n in bad["nodes"]}
    labels = incomplete_prereq_labels(bad, fresh, "production_ml_systems")
    assert labels == ["ML Fundamentals"]


def test_read_time_guard_is_loud_not_silent():
    _warn_dangling.cache_clear()  # the warning is deduped per distinct dangling-edge set
    import logging
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("services.completion")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        prereqs_by_node(_incoherent_graph())
    finally:
        logger.removeHandler(handler)
    assert any("mlops_tools" in m for m in records)


def test_healthy_graph_is_completely_unaffected():
    good = _healthy_graph()
    _warn_dangling.cache_clear()
    before = _warn_dangling.cache_info().currsize

    assert prereqs_by_node(good) == {
        "python_basics": set(),
        "ml_fundamentals": {"python_basics"},
        "production_ml_systems": {"ml_fundamentals"},
        "model_deployment_and_monitoring": set(),
    }
    assert _warn_dangling.cache_info().currsize == before  # nothing dropped -> nothing warned
    assert is_session_complete(good, _all_mastered(good)) is True


def test_prune_drops_only_dangling_edges_and_is_immutable():
    bad = _incoherent_graph()
    original_edges = [dict(e) for e in bad["edges"]]

    repaired, dropped = prune_dangling_edges(bad)

    assert bad["edges"] == original_edges  # input untouched
    assert len(dropped) == 3
    assert all("mlops_tools" in (e["source"], e["target"]) for e in dropped)
    assert repaired["nodes"] == bad["nodes"]
    assert repaired["summary"] == bad["summary"]

    node_ids = {n["id"] for n in repaired["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in repaired["edges"])
    # The good edges survive in order.
    assert repaired["edges"] == [original_edges[0], original_edges[1]]


def test_prune_is_a_noop_on_a_coherent_graph():
    repaired, dropped = prune_dangling_edges(_incoherent_graph() | {"edges": []})
    assert dropped == []
    assert repaired["edges"] == []


def test_node_whose_only_prereq_was_dangling_becomes_reachable():
    """Asserted through the real consumers (locked_node_ids / complete_node_ids), not the edge list."""
    repaired, _ = prune_dangling_edges(_incoherent_graph())
    fresh = {n["id"]: {"status": "available", "best_score": 0} for n in repaired["nodes"]}

    # model_deployment_and_monitoring's ONLY prerequisite was the phantom: it is now a root.
    assert "model_deployment_and_monitoring" not in locked_node_ids(repaired, fresh)
    # production_ml_systems keeps its real prerequisite chain — still locked while unmastered.
    assert "production_ml_systems" in locked_node_ids(repaired, fresh)


def test_is_session_complete_can_fire_on_a_repaired_graph():
    repaired, _ = prune_dangling_edges(_incoherent_graph())
    state = _all_mastered(repaired)
    assert locked_node_ids(repaired, state) == set()
    assert complete_node_ids(repaired, state) == {n["id"] for n in repaired["nodes"]}
    assert is_session_complete(repaired, state) is True


async def test_consensus_node_drops_dangling_edges_at_build(monkeypatch, caplog):
    """End-to-end through the compiled graph: the persisted skill_graph is already repaired."""
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))

    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="SCOUT", Reasoning="r")
    scout = MagicMock()
    scout_out = MagicMock()
    scout_out.model_dump.return_value = {
        "sub_agent_queries": {"ACADEMIC": "a", "MARKET": "m", "PRACTICAL": "p"}
    }
    scout.generate_specialized_queries.return_value = scout_out
    academic = MagicMock()
    academic.provide_academic_roadmap.return_value = MagicMock(content="A")
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="P")
    market = MagicMock()
    market.extract_job_title = AsyncMock(return_value="ML Engineer")
    market.scraper.initialize = AsyncMock()
    market.scraper.search_jobs = AsyncMock(return_value="ds1")
    market.scraper.fetch_job_results = AsyncMock(return_value=[{"title": "ML Engineer"}])
    market.summarize_job = AsyncMock(return_value="JOB")
    consensus = MagicMock()
    sg = MagicMock()
    sg.model_dump.return_value = _incoherent_graph()
    consensus.build_skill_graph.return_value = sg
    reviewer = MagicMock()
    reviewer.review_skill_graph.return_value = MagicMock(passed=True, notes="ok")

    compiled = build_graph(
        orchestrator=orchestrator, scout=scout, academic=academic, market=market,
        practical=practical, consensus=consensus, reviewer=reviewer,
    )
    with caplog.at_level("WARNING"):
        state = await compiled.ainvoke({"user_query": "learn ML engineering"})

    node_ids = {n["id"] for n in state["skill_graph"]["nodes"]}
    assert "mlops_tools" not in node_ids
    assert all(
        e["source"] in node_ids and e["target"] in node_ids
        for e in state["skill_graph"]["edges"]
    )
    # Loud, not silent.
    assert any("mlops_tools" in r.getMessage() for r in caplog.records)
    # The Reviewer receives the REPAIRED graph, so this class of incoherence never reaches it.
    assert "mlops_tools" not in reviewer.review_skill_graph.call_args[0][1]
    # ...and the phantom no longer renders.
    assert "mlops_tools" not in state["skill_graph_mermaid"]


async def test_reviewer_rejection_is_logged_loudly(monkeypatch, caplog):
    """review_passed:False is inert downstream — at minimum it must be visible in the logs."""
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))

    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="SCOUT", Reasoning="r")
    scout = MagicMock()
    scout_out = MagicMock()
    scout_out.model_dump.return_value = {"sub_agent_queries": {}}
    scout.generate_specialized_queries.return_value = scout_out
    academic = MagicMock()
    academic.provide_academic_roadmap.return_value = MagicMock(content="A")
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="P")
    market = MagicMock()
    market.extract_job_title = AsyncMock(return_value="X")
    market.scraper.initialize = AsyncMock()
    market.scraper.search_jobs = AsyncMock(return_value=None)
    consensus = MagicMock()
    sg = MagicMock()
    sg.model_dump.return_value = _incoherent_graph() | {"edges": []}
    consensus.build_skill_graph.return_value = sg
    reviewer = MagicMock()
    reviewer.review_skill_graph.return_value = MagicMock(passed=False, notes="Not coherent.")

    compiled = build_graph(
        orchestrator=orchestrator, scout=scout, academic=academic, market=market,
        practical=practical, consensus=consensus, reviewer=reviewer,
    )
    with caplog.at_level("WARNING"):
        state = await compiled.ainvoke({"user_query": "learn X"})

    assert state["review_passed"] is False
    assert any("Reviewer REJECTED" in r.getMessage() for r in caplog.records)
