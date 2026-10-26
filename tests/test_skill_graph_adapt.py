"""Phase 3 — deterministic merge: apply_adaptation preserves ids, appends, dedups (no LLM)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from graph.skill_graph_adapt import apply_adaptation


def _graph():
    return {
        "summary": "roadmap",
        "nodes": [
            {"id": "loops", "label": "Loops", "description": "iteration", "level": "foundational"},
            {"id": "funcs", "label": "Functions", "description": "callables", "level": "intermediate"},
        ],
        "edges": [{"source": "loops", "target": "funcs", "relation": "prerequisite"}],
    }


def test_low_score_adds_remedial_node_and_inbound_edge():
    adaptation = {
        "new_nodes": [{"id": "loop_basics", "label": "Loop Basics", "description": "off-by-one", "level": "foundational"}],
        "new_edges": [{"source": "loop_basics", "target": "loops", "relation": "prerequisite"}],
        "remediation_focus": ["off-by-one"],
        "rationale": "remedial",
    }
    new_graph, new_ids = apply_adaptation(_graph(), adaptation)
    ids = [n["id"] for n in new_graph["nodes"]]
    assert ids == ["loops", "funcs", "loop_basics"]
    assert new_ids == ["loop_basics"]
    assert {"source": "loop_basics", "target": "loops", "relation": "prerequisite"} in new_graph["edges"]


def test_existing_id_is_never_overwritten():
    adaptation = {
        "new_nodes": [{"id": "loops", "label": "HIJACK", "description": "x", "level": "advanced"}],
        "new_edges": [],
        "remediation_focus": [],
        "rationale": "",
    }
    new_graph, new_ids = apply_adaptation(_graph(), adaptation)
    assert new_ids == []
    loops = next(n for n in new_graph["nodes"] if n["id"] == "loops")
    assert loops["label"] == "Loops"  # original preserved, not hijacked


def test_duplicate_edge_is_not_appended():
    adaptation = {
        "new_nodes": [],
        "new_edges": [{"source": "loops", "target": "funcs", "relation": "prerequisite"}],
        "remediation_focus": [],
        "rationale": "",
    }
    new_graph, _ = apply_adaptation(_graph(), adaptation)
    assert len(new_graph["edges"]) == 1


def test_high_score_adds_unlock_edge():
    adaptation = {
        "new_nodes": [],
        "new_edges": [{"source": "loops", "target": "funcs", "relation": "unlock"}],
        "remediation_focus": [],
        "rationale": "mastered",
    }
    new_graph, new_ids = apply_adaptation(_graph(), adaptation)
    assert new_ids == []
    assert any(e.get("relation") == "unlock" for e in new_graph["edges"])


def test_edge_referencing_unknown_node_is_dropped():
    adaptation = {
        "new_nodes": [],
        "new_edges": [{"source": "ghost", "target": "loops", "relation": "prerequisite"}],
        "remediation_focus": [],
        "rationale": "",
    }
    new_graph, _ = apply_adaptation(_graph(), adaptation)
    assert len(new_graph["edges"]) == 1  # orphan edge skipped


def test_input_graph_is_not_mutated():
    g = _graph()
    adaptation = {
        "new_nodes": [{"id": "x", "label": "X", "description": "x", "level": "foundational"}],
        "new_edges": [{"source": "x", "target": "loops", "relation": "prerequisite"}],
        "remediation_focus": [],
        "rationale": "",
    }
    apply_adaptation(g, adaptation)
    assert len(g["nodes"]) == 2  # original untouched
    assert len(g["edges"]) == 1


# --- synthesized inbound edge (orphaned remedial nodes never dead-end the graded node) ---------

def test_orphan_remedial_node_gets_synthesized_inbound_edge():
    # LLM omits the edge into the graded node entirely (the qwen2.5:7b regression).
    adaptation = {
        "new_nodes": [{"id": "loop_basics", "label": "Loop Basics", "description": "x", "level": "foundational"}],
        "new_edges": [],
        "remediation_focus": ["off-by-one"],
        "rationale": "remedial",
    }
    new_graph, new_ids = apply_adaptation(_graph(), adaptation, graded_node_id="loops")
    assert new_ids == ["loop_basics"]
    assert {"source": "loop_basics", "target": "loops", "relation": "prerequisite"} in new_graph["edges"]


def test_chained_remedial_nodes_only_the_sink_points_into_graded_node():
    # setup_environment -> verify_setup, chained only to each other, neither pointing at the graded
    # node ("python_setup") — the observed live qwen2.5:7b failure. Only the end of the chain
    # (verify_setup, the sink) should get wired in; setup_environment reaches transitively.
    graph = {
        "summary": "roadmap",
        "nodes": [{"id": "python_setup", "label": "Python Setup", "description": "x", "level": "foundational"}],
        "edges": [],
    }
    adaptation = {
        "new_nodes": [
            {"id": "setup_environment", "label": "Setup Env", "description": "x", "level": "foundational"},
            {"id": "verify_setup", "label": "Verify Setup", "description": "x", "level": "foundational"},
        ],
        "new_edges": [{"source": "setup_environment", "target": "verify_setup", "relation": "prerequisite"}],
        "remediation_focus": ["environment"],
        "rationale": "remedial",
    }
    new_graph, new_ids = apply_adaptation(graph, adaptation, graded_node_id="python_setup")
    assert set(new_ids) == {"setup_environment", "verify_setup"}
    synthesized = [e for e in new_graph["edges"] if e["target"] == "python_setup"]
    assert synthesized == [{"source": "verify_setup", "target": "python_setup", "relation": "prerequisite"}]
    # setup_environment must NOT get a redundant direct edge — it already reaches via verify_setup.
    assert not any(e["source"] == "setup_environment" and e["target"] == "python_setup" for e in new_graph["edges"])

    from services.completion import locked_node_ids, complete_node_ids

    state = {
        "python_setup": {"status": "needs_review", "remediation_pending": True},
        "setup_environment": {"status": "available"},
        "verify_setup": {"status": "available"},
    }
    assert "python_setup" in locked_node_ids(new_graph, state)  # dead end before completion
    state["setup_environment"]["status"] = "mastered"
    state["verify_setup"]["status"] = "mastered"
    complete = complete_node_ids(new_graph, state)
    assert {"setup_environment", "verify_setup"} <= complete
    assert "python_setup" not in locked_node_ids(new_graph, state)  # unlocked once the chain is done


def test_two_independent_remedial_nodes_each_get_their_own_inbound_edge():
    # No edges between the new nodes at all -> each is its own one-node chain -> each gets wired in.
    adaptation = {
        "new_nodes": [
            {"id": "a_basics", "label": "A Basics", "description": "x", "level": "foundational"},
            {"id": "b_basics", "label": "B Basics", "description": "x", "level": "foundational"},
        ],
        "new_edges": [],
        "remediation_focus": ["gap1", "gap2"],
        "rationale": "remedial",
    }
    new_graph, new_ids = apply_adaptation(_graph(), adaptation, graded_node_id="loops")
    assert set(new_ids) == {"a_basics", "b_basics"}
    targets = {(e["source"], e["target"]) for e in new_graph["edges"] if e["target"] == "loops"}
    assert targets == {("a_basics", "loops"), ("b_basics", "loops")}


def test_llm_supplied_inbound_edge_is_not_duplicated():
    # LLM already did the right thing -> no synthesized edge should be added on top.
    adaptation = {
        "new_nodes": [{"id": "loop_basics", "label": "Loop Basics", "description": "x", "level": "foundational"}],
        "new_edges": [{"source": "loop_basics", "target": "loops", "relation": "prerequisite"}],
        "remediation_focus": ["off-by-one"],
        "rationale": "remedial",
    }
    new_graph, _ = apply_adaptation(_graph(), adaptation, graded_node_id="loops")
    matches = [e for e in new_graph["edges"] if e["source"] == "loop_basics" and e["target"] == "loops"]
    assert len(matches) == 1


def test_no_graded_node_id_leaves_orphan_unwired():
    # Without graded_node_id (e.g. the mastered/unlock path), no synthesis happens — behavior unchanged.
    adaptation = {
        "new_nodes": [{"id": "loop_basics", "label": "Loop Basics", "description": "x", "level": "foundational"}],
        "new_edges": [],
        "remediation_focus": [],
        "rationale": "",
    }
    new_graph, new_ids = apply_adaptation(_graph(), adaptation)
    assert new_ids == ["loop_basics"]
    assert not any(e["target"] == "loops" for e in new_graph["edges"] if e["source"] == "loop_basics")


def test_accepts_pydantic_adaptation():
    from agents.adaptation.adaptation_schema import GraphAdaptation

    adaptation = GraphAdaptation(
        new_nodes=[{"id": "n3", "label": "N3", "description": "d", "level": "foundational"}],
        new_edges=[{"source": "n3", "target": "loops", "relation": "prerequisite"}],
        remediation_focus=["gap"],
        rationale="r",
    )
    new_graph, new_ids = apply_adaptation(_graph(), adaptation)
    assert new_ids == ["n3"]
