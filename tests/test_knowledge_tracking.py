"""Numerical and lifecycle checks for experimental exercise-level knowledge tracing."""
import json

import pytest

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from persistence.repository import InMemoryLearningSessionRepository
from services import grading_gateway as gateway, knowledge_tracking as tracking
from services.mastery import default_node_state
from services.public_projection import public_session


def exercise(identity="task"):
    return {"id": identity, "revision": identity + "-r1", "format": "coding_challenge", "language": "json",
            "statement": "Return a=42 and b=6 as JSON.", "grading_artifact": {"method": "structured", "checks": [
                {"id": "a", "answer_key": "a", "expected": 42, "max_points": 50},
                {"id": "b", "answer_key": "b", "expected": 6, "max_points": 50}]}}


def install(repo, user="u", sid="s", node="a", identity="task"):
    ls = repo.get(user, sid)
    if ls is None:
        ls = {"skill_graph": {"nodes": [{"id": node, "label": "Multiplication"}], "edges": []},
              "node_state": {node: default_node_state()}, "events": [], "lessons": {}}
    ex = exercise(identity)
    gateway.publish_exercise(repo, user, ex, context={"session_id": sid, "node_id": node})
    ls["lessons"][node] = {"content": "# Multiplication", "exercise": ex}
    repo.save(user, sid, ls)
    return ex


def submit(repo, ex, *, user="u", sid="s", node="a", attempt="once", success=True):
    return gateway.grade_lesson(repo, user, sid, node, json.dumps({"a": 42 if success else 0, "b": 6}),
                                ex["revision"], attempt)


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setenv("MINDMORPH_BKT", "1")
    monkeypatch.setattr("services.learning_service.adapt_after_grade", lambda *args: [])
    return InMemoryLearningSessionRepository()


@pytest.mark.parametrize("outcomes, expected", [
    ([], []),
    ([False, False, True], [0.1272727272727273, 0.11611253196930947, 0.4343698854337152]),
    ([True, True, True], [0.5764705882352941, 0.8736842105263158, 0.9719844357976654]),
])
def test_pybkt_post_observation_states_match_hand_calculated_examples(outcomes, expected):
    assert tracking.predict_history(outcomes) == pytest.approx(expected)


def test_probabilities_are_bounded_for_long_sequences_and_mistake_lowers_estimate():
    predictions = tracking.predict_history([True] * 5 + [False] + [True] * 100)
    assert all(0 <= value <= 1 for value in predictions)
    assert predictions[5] < predictions[4]
    with pytest.raises(ValueError):
        tracking.predict_history([1])


@pytest.mark.parametrize("p,count,mode", [(0.59, 3, "foundation"), (0.6, 3, "standard"),
    (0.89999, 3, "standard"), (0.9, 3, "challenge"), (0.99, 2, "standard")])
def test_recommendation_boundaries(p, count, mode):
    assert tracking.recommendation(p, count).mode == mode


def test_same_latest_score_produces_different_guidance_without_changing_mastery(repo):
    for user, history in [("rahul", [False, False, True]), ("priya", [True, True, True])]:
        for index, success in enumerate(history):
            ex = install(repo, user=user, identity=f"exercise-{index}")
            ls, _, result = submit(repo, ex, user=user, attempt=f"attempt-{index}", success=success)
            summary = ls["node_state"]["a"]["knowledge_tracking"]
            assert summary["observation_count"] == index + 1
            if index < 2:
                assert summary["probability"] is None and summary["status"] == "insufficient_evidence"
        assert result["score"] == 100 and ls["node_state"]["a"]["status"] == "mastered"
    rahul = repo.get("rahul", "s")["node_state"]["a"]["knowledge_tracking"]
    priya = repo.get("priya", "s")["node_state"]["a"]["knowledge_tracking"]
    assert rahul["probability"] == pytest.approx(0.4343698854)
    assert priya["probability"] == pytest.approx(0.9719844358)
    assert rahul["recommendation"]["mode"] == "foundation"
    assert priya["recommendation"]["mode"] == "challenge"


def test_replays_resubmissions_and_evaluator_revisions_do_not_inflate_evidence(repo):
    ex = install(repo)
    submit(repo, ex, success=False)
    submit(repo, ex, success=False)  # HTTP replay
    submit(repo, ex, attempt="improved", success=True)  # New submission, same task
    newer = repo.get("u", "s")
    repaired = exercise()
    repaired["revision"] = "repaired"
    gateway.publish_exercise(repo, "u", repaired, context={"session_id": "s", "node_id": "a"})
    newer["lessons"]["a"]["exercise"] = repaired
    repo.save("u", "s", newer)
    submit(repo, repaired, attempt="repaired-attempt")
    observations = GradingStore(repo).knowledge_observations("u", "s", "a")
    assert len(observations) == 1 and observations[0]["successful"] is False
    assert repo.get("u", "s")["node_state"]["a"]["status"] == "mastered"


def test_conflict_rolls_back_observation_and_reconciliation_applies_once(repo, monkeypatch):
    ex = install(repo)
    evaluate = gateway._evaluate
    def concurrently(*args):
        current = repo.get("u", "s")
        current["chat"] = [{"role": "user", "content": "Retain this note"}]
        repo.save("u", "s", current)
        return evaluate(*args)
    monkeypatch.setattr(gateway, "_evaluate", concurrently)
    with pytest.raises(SessionConflictError):
        submit(repo, ex)
    store = GradingStore(repo)
    assert store.knowledge_observations("u", "s", "a") == []
    assert "knowledge_tracking" not in repo.get("u", "s")["node_state"]["a"]
    for _ in range(2):
        gateway.reconcile_lesson(repo, "u", "s", "a", "once")
    ls = repo.get("u", "s")
    assert len(store.knowledge_observations("u", "s", "a")) == 1
    assert ls["node_state"]["a"]["attempts"] == 1
    assert ls["chat"][0]["content"] == "Retain this note"


def test_model_failure_preserves_evidence_and_recovers_on_next_grade(repo, monkeypatch):
    original = tracking.predict_history
    def broken(*args):
        raise ImportError("Simulated dependency failure")
    monkeypatch.setattr(tracking, "predict_history", broken)
    ex = install(repo)
    ls, _, result = submit(repo, ex)
    assert result["score"] == 100 and ls["node_state"]["a"]["status"] == "mastered"
    assert ls["node_state"]["a"]["knowledge_tracking"]["status"] == "unavailable"
    assert len(GradingStore(repo).knowledge_observations("u", "s", "a")) == 1
    monkeypatch.setattr(tracking, "predict_history", original)
    # The same exercise is not counted again, but its saved observation is replayed.
    ls, _, _ = submit(repo, ex, attempt="recovery")
    summary = ls["node_state"]["a"]["knowledge_tracking"]
    assert summary["status"] == "insufficient_evidence" and summary["observation_count"] == 1


@pytest.mark.parametrize("outcome", ["pending", "platform_error", "retryable_error", "invalid_submission", "feedback_only"])
def test_noneligible_outcomes_never_create_observations(repo, monkeypatch, outcome):
    ex = install(repo)
    monkeypatch.setattr(gateway, "_evaluate", lambda *args: gateway.outcome(outcome, "No dependable result"))
    submit(repo, ex)
    assert GradingStore(repo).knowledge_observations("u", "s", "a") == []
    assert repo.get("u", "s")["node_state"]["a"]["attempts"] == 0


def test_disabled_flag_does_not_track_or_expose_saved_recommendations(repo, monkeypatch):
    ex = install(repo)
    submit(repo, ex)
    monkeypatch.setenv("MINDMORPH_BKT", "0")
    other = install(repo, identity="second")
    ls, _, _ = submit(repo, other, attempt="disabled")
    assert len(GradingStore(repo).knowledge_observations("u", "s", "a")) == 1
    assert "knowledge_tracking" not in public_session(ls)["node_state"]["a"]
    assert tracking.guidance_prompt({"mode": "challenge", "reason": "History"}) == ""


def test_history_is_isolated_by_user_session_node_and_repository(repo):
    for user, sid, node in [("u", "s", "a"), ("other", "s", "a"), ("u", "other", "a"), ("u", "s2", "b")]:
        ex = install(repo, user=user, sid=sid, node=node)
        submit(repo, ex, user=user, sid=sid, node=node, attempt=f"{sid}-{node}")
    store = GradingStore(repo)
    rows = store.knowledge_observations("u", "s", "a")
    assert len(rows) == 1 and rows[0]["session_id"] == "s" and rows[0]["node_id"] == "a"
    assert store.knowledge_observations("u", "s", "b") == []
    assert GradingStore(InMemoryLearningSessionRepository()).knowledge_observations("u", "s", "a") == []


def test_public_summary_strips_private_fields_and_rejects_inconsistent_estimates(repo):
    ls = {"node_state": {"a": {"knowledge_tracking": {**tracking.summarize([]), "private": "secret"}}}, "lessons": {}}
    assert "private" not in public_session(ls)["node_state"]["a"]["knowledge_tracking"]
    ls["node_state"]["a"]["knowledge_tracking"] = {**tracking.summarize([]), "status": "ready", "probability": 1}
    assert public_session(ls)["node_state"]["a"]["knowledge_tracking"]["status"] == "unavailable"


def test_standalone_and_nonfinal_attempts_cannot_enter_tracking(repo):
    ls = {"node_state": {"a": default_node_state()}}
    result = {"status": "graded", "method": "execution", "passed": 1, "total": 1, "failures": []}
    assert tracking.attempt_scope(ls, "s", {"state": "completed", "context": {"standalone": True},
        "result": result, "exercise_id": "e"}) is None
    assert tracking.attempt_scope(ls, "s", {"state": "running", "context": {"session_id": "s", "node_id": "a"},
        "result": result, "exercise_id": "e"}) is None
    assert tracking.attempt_scope(ls, "s", {"kind": "run", "state": "completed",
        "context": {"session_id": "s", "node_id": "a"}, "result": result, "exercise_id": "e"}) is None


def test_low_grade_retains_existing_remediation_lock(repo, monkeypatch):
    from services.learning_service import LockedNodeError
    from services.practice_tasks import replace_task
    ex = install(repo)
    monkeypatch.setattr(gateway, "_evaluate", lambda *args: {"status": "graded", "method": "execution",
        "passed": 1, "total": 4, "failures": ["A private check did not pass."]})
    ls, _, result = submit(repo, ex)
    assert result["score"] == 25
    assert ls["node_state"]["a"]["status"] == "needs_review"
    assert ls["node_state"]["a"]["remediation_pending"]
    assert len(GradingStore(repo).knowledge_observations("u", "s", "a")) == 1
    with pytest.raises(LockedNodeError):
        replace_task(repo, "u", "s", "a", ex["revision"], "cannot-bypass-lock")
