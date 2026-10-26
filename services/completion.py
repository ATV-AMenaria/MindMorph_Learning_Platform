"""Prerequisite-gated completion — pure skill-graph derivations (no Streamlit, no LLM).

Per-node mastery (``best_score >= 80``, sticky) is the underlying truth. "Complete" is derived: a node
counts as complete only when it AND all its prerequisites (transitively) are mastered. A node that
passed its own exercise but still has an incomplete prerequisite renders as 'blocked' (🔒), not
'mastered' (✅), and is excluded from the progress count. Extracted from ``app.py``; unit-tested via
``tests/test_completion_gating.py``.
"""
import logging
from functools import lru_cache

from graph.skill_graph_adapt import prune_dangling_edges

logger = logging.getLogger(__name__)

# Picker ordering: foundational -> intermediate -> advanced. Phase 3 appends remedial nodes to the
# end of the nodes list; sorting by level rank (stable within a level) realigns the picker with the
# graph's reading order.
_LEVEL_RANK = {"foundational": 0, "intermediate": 1, "advanced": 2}


def node_label_map(skill_graph: dict) -> dict:
    """Map node_id -> label for the skill picker (ids are unique; labels may repeat)."""
    return {n["id"]: n.get("label", n["id"]) for n in skill_graph.get("nodes", [])}


def ordered_node_ids(skill_graph: dict) -> list:
    nodes = skill_graph.get("nodes", [])
    return [
        n["id"]
        for n in sorted(nodes, key=lambda n: _LEVEL_RANK.get((n.get("level") or "").lower(), 1))
    ]


@lru_cache(maxsize=256)
def _warn_dangling(dropped: tuple) -> None:
    """Warn once per distinct set of dangling edges (this runs several times per request).

    # ponytail: once per shape per process, and no session/query context (unlike the build-time log
    # in consensus_node) — the signature has no session handle. Thread a session id through if an
    # operator ever needs to identify WHICH learner is on a rescued graph.
    """
    logger.warning(
        "Skill graph loaded with %d edge(s) referencing unknown node ids: %s — ignored at read time "
        "(stored session left as-is; see graph/skill_graph_adapt.prune_dangling_edges)",
        len(dropped), list(dropped),
    )


def prereqs_by_node(skill_graph: dict) -> dict:
    """Map node_id -> set of its prerequisite ids. Edge convention (SkillEdge): source = prerequisite,
    target = the skill that depends on it. So prereqs of X = sources of edges whose target is X.

    Read-time enforcement of the same invariant ``prune_dangling_edges`` applies at BUILD time, reusing
    that function so there is one implementation. Build-time pruning only protects graphs built after
    it landed; a session persisted earlier with a phantom SOURCE (e.g. ``mlops_tools -> ...``) carries
    an unmasterable prerequisite forever — its dependants stay locked and ``is_session_complete`` can
    never fire. Ignoring those edges here makes such a session finishable without rewriting stored
    state. Healthy graphs are unaffected (nothing is dropped), and a phantom TARGET was already
    discarded by the ``tgt in prereqs`` guard, so this only ever removes an unsatisfiable gate."""
    graph, dropped = prune_dangling_edges(skill_graph)
    if dropped:
        _warn_dangling(tuple(sorted((str(e.get("source")), str(e.get("target"))) for e in dropped)))
    prereqs: dict = {n["id"]: set() for n in graph.get("nodes", [])}
    for e in graph.get("edges", []) or []:
        src, tgt = e.get("source"), e.get("target")
        if tgt in prereqs and src is not None:
            prereqs[tgt].add(src)
    return prereqs


def complete_node_ids(skill_graph: dict, node_state: dict) -> set:
    """Set of node ids that are fully complete: mastered AND every prerequisite complete (recursive).

    Memoized with a cycle guard — a node currently being resolved is treated as not-complete, so a
    cyclic graph returns instead of recursing forever (the graph is meant to be acyclic).
    """
    prereqs = prereqs_by_node(skill_graph)

    def _is_mastered(nid: str) -> bool:
        return node_state.get(nid, {}).get("status") == "mastered"

    memo: dict = {}
    visiting: set = set()

    def _complete(nid: str) -> bool:
        if nid in memo:
            return memo[nid]
        if nid in visiting:  # cycle: don't recurse, count as not-complete
            return False
        if not _is_mastered(nid):
            memo[nid] = False
            return False
        visiting.add(nid)
        result = all(_complete(p) for p in prereqs.get(nid, set()))
        visiting.discard(nid)
        memo[nid] = result
        return result

    return {nid for nid in prereqs if _complete(nid)}


def is_session_complete(skill_graph: dict, node_state: dict) -> bool:
    """True when every node in the graph is fully complete (the whole path is mastered).

    Used to fire the ``path_completed`` funnel event once the learner finishes. Reuses the memoized
    ``complete_node_ids`` derivation so the definition of "complete" stays single-sourced.
    """
    nodes = skill_graph.get("nodes", [])
    if not nodes:
        return False
    all_ids = {n["id"] for n in nodes}
    return complete_node_ids(skill_graph, node_state) >= all_ids


def _remediation_locked(skill_graph: dict, node_state: dict, complete: set) -> set:
    """Nodes deterministically locked by a sub-40 grade (``remediation_pending``), independent of the
    LLM. The flag locks the node UNTIL its remedial prerequisites exist AND are all complete — so a
    node whose remediation LLM call failed (no prereqs yet) stays locked rather than slipping open.
    A node with no prerequisites and the flag set is locked (the remediation has not landed yet)."""
    prereqs = prereqs_by_node(skill_graph)
    locked = set()
    for nid, st in node_state.items():
        if not st.get("remediation_pending"):
            continue
        ps = prereqs.get(nid, set())
        satisfied = bool(ps) and all(p in complete for p in ps)
        if not satisfied:
            locked.add(nid)
    return locked


def locked_node_ids(skill_graph: dict, node_state: dict) -> set:
    """Set of node ids that are LOCKED.

    Two lock sources, unioned: (a) derived — at least one direct prerequisite is not complete
    (transitivity is automatic, since completion is transitive); (b) deterministic remediation — a
    sub-40 grade set ``remediation_pending`` and the node's remedial prerequisites aren't yet complete.
    A learner may not open a locked node's lesson. Root nodes (no prerequisites, no flag) are never locked.
    """
    complete = complete_node_ids(skill_graph, node_state)
    prereqs = prereqs_by_node(skill_graph)
    derived = {nid for nid, ps in prereqs.items() if any(p not in complete for p in ps)}
    return derived | _remediation_locked(skill_graph, node_state, complete)


def incomplete_prereq_labels(skill_graph: dict, node_state: dict, node_id: str) -> list:
    """Labels of a node's direct prerequisites that are not yet complete (for the lock message)."""
    complete = complete_node_ids(skill_graph, node_state)
    label_by_id = node_label_map(skill_graph)
    prereqs = prereqs_by_node(skill_graph).get(node_id, set())
    return [label_by_id.get(p, p) for p in prereqs if p not in complete]


def display_status(skill_graph: dict, node_state: dict) -> dict:
    """node_id -> status handed to the renderer. Complete -> 'mastered' (✅); any node with an
    incomplete prerequisite (locked) -> 'blocked' (🔒); otherwise the underlying status (unchanged).
    The mastered-but-prereq-pending case is a subset of locked, so its 🔒 behavior is preserved."""
    complete = complete_node_ids(skill_graph, node_state)
    locked = locked_node_ids(skill_graph, node_state)
    out: dict = {}
    for nid, s in node_state.items():
        if nid in complete:
            out[nid] = "mastered"
        elif nid in locked:
            out[nid] = "blocked"
        else:
            out[nid] = s.get("status")
    return out
