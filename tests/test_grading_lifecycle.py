"""Offline acceptance for private publication, saved attempts and progress gates."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from persistence.repository import InMemoryLearningSessionRepository
from services import grading_gateway as gateway
from services.grading_results import normalize_grade_result
from services.learning_service import grade_assessment, _validate_quiz
from services.mastery import apply_score, default_node_state
from services.public_projection import public_session


def session():
    return {"skill_graph": {"nodes": [{"id": "a", "label": "A"}], "edges": []},
        "node_state": {"a": default_node_state()}, "events": [], "lessons": {"a": {"exercise": {
            "id": "task", "revision": "r1", "format": "coding_challenge", "language": "python",
            "statement": "Compute the requested value", "grading_artifact": {"unit_tests": ["PRIVATE TEST"],
            "reference_files": {"main.py": "PRIVATE REFERENCE"}}}}}}


def valid_result():
    return {"status": "graded", "method": "execution", "complete": True, "passed": 2, "total": 2, "failures": []}


@pytest.mark.parametrize("raw", [{}, {"score": 100}, {"status": "graded", "score": float("nan")},
    {"status": "graded", "passed": 0, "total": 0},
    {"status": "graded", "passed": 2, "total": 1},
    {"status": "graded", "passed": 1, "total": 1, "failures": ["contradiction"]},
    {"status": "feedback_only", "score": 100}, {"status": "pending", "score": 100}])
def test_malformed_or_nonfinal_result_never_changes_progress(raw):
    ls = session()
    before = dict(ls["node_state"]["a"])
    apply_score(ls, "a", "coding_challenge", raw)
    assert ls["node_state"]["a"] == before


def test_public_projection_strips_private_artifacts_and_presubmit_keys():
    ls = session()
    ls["assessment"] = {"quiz": {"questions": [{"id": "q", "node_id": "a", "question": "Which?",
        "options": ["A", "B"], "correct_index": 1, "explanation": "PRIVATE KEY"}]},
        "status": "pending", "revision": "quiz1"}
    response = public_session(ls)
    assert "grading_artifact" not in response["lessons"]["a"]["exercise"]
    assert "correct_index" not in response["assessment"]["quiz"]["questions"][0]
    assert response["assessment"]["quiz_revision"] == "quiz1"
    ls["assessment"].update(status="submitted", submitted=True)
    assert public_session(ls)["assessment"]["quiz"]["questions"][0]["correct_index"] == 1


def test_private_packages_are_scoped_and_memory_instances_are_isolated():
    repo, other = InMemoryLearningSessionRepository(), InMemoryLearningSessionRepository()
    ls = session()
    gateway.synchronize_session(repo, "u", "s", ls)
    ex = ls["lessons"]["a"]["exercise"]
    assert "grading_artifact" not in ex
    assert GradingStore(repo).get("u", "package:" + ex["package_id"])["artifact"]["unit_tests"] == ["PRIVATE TEST"]
    assert GradingStore(repo).get("foreign", "package:" + ex["package_id"]) is None
    assert GradingStore(other).get("u", "package:" + ex["package_id"]) is None


def test_replay_and_mismatch_do_not_rejudge_or_apply_twice(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    repo.save("u", "s", session())
    calls = []
    monkeypatch.setattr(gateway, "_evaluate", lambda *args: calls.append(1) or valid_result())
    monkeypatch.setattr("services.learning_service.adapt_after_grade", lambda *args: [])
    ls, _, result = gateway.grade_lesson(repo, "u", "s", "a", "code", "r1", "attempt1")
    assert result["score"] == 100 and ls["node_state"]["a"]["attempts"] == 1
    ls, _, result = gateway.grade_lesson(repo, "u", "s", "a", "code", "r1", "attempt1")
    assert result["applied"] and ls["node_state"]["a"]["attempts"] == 1 and len(calls) == 1
    with pytest.raises(SessionConflictError):
        gateway.grade_lesson(repo, "u", "s", "a", "different code", "r1", "attempt1")
    assert len(calls) == 1


def test_concurrent_same_attempt_claim_dispatches_once(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    ex = session()["lessons"]["a"]["exercise"]
    gateway.publish_exercise(repo, "u", ex, context={"standalone": True})
    entered, release = Event(), Event()
    calls = []
    def evaluate(*args):
        calls.append(1); entered.set(); assert release.wait(10)
        return valid_result()
    monkeypatch.setattr(gateway, "_evaluate", evaluate)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(gateway.accept_and_evaluate, repo, "u", ex, "code", "r1", "same", context={"standalone": True})
        assert entered.wait(10)
        replay, created = gateway.accept_and_evaluate(repo, "u", ex, "code", "r1", "same", context={"standalone": True})
        assert not created and replay["state"] == "running"
        release.set(); first.result()
    assert len(calls) == 1


def test_conflict_retains_terminal_result_and_reconciles_without_rejudge(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    repo.save("u", "s", session())
    calls = []
    def evaluate(*args):
        calls.append(1)
        concurrent = repo.get("u", "s")
        concurrent["chat"] = [{"role": "user", "content": "Concurrent update"}]
        repo.save("u", "s", concurrent)
        return valid_result()
    monkeypatch.setattr(gateway, "_evaluate", evaluate)
    monkeypatch.setattr("services.learning_service.adapt_after_grade", lambda *args: [])
    with pytest.raises(SessionConflictError):
        gateway.grade_lesson(repo, "u", "s", "a", "code", "r1", "conflict")
    attempt = GradingStore(repo).get("u", "attempt:conflict")
    assert attempt["state"] == "completed" and not attempt["applied"]
    assert repo.get("u", "s")["node_state"]["a"]["attempts"] == 0
    ls, _, result = gateway.reconcile_lesson(repo, "u", "s", "a", "conflict")
    assert result["applied"] and ls["node_state"]["a"]["attempts"] == 1 and len(calls) == 1
    assert ls["chat"][0]["content"] == "Concurrent update"


def test_uncalibrated_rubric_cannot_self_certify():
    raw = {"status": "graded", "calibration_verified": True, "score": 100,
        "criteria": [{"criterion_id": "c1", "earned_points": 100, "max_points": 100, "evidence": ["quote"]}]}
    result = normalize_grade_result(raw, method="rubric")
    assert result["status"] == "feedback_only" and result["score"] is None and not result["mastery_eligible"]


def test_skip_resume_does_not_score_and_invalid_legacy_quiz_can_skip():
    ls = session()
    ls["assessment"] = {"revision": "q1", "quiz": {"questions": [{"node_id": "phantom"}]}, "submitted": False}
    grade_assessment(ls, action="skip", quiz_revision="q1")
    assert ls["assessment"]["status"] == "skipped" and not ls["events"]
    grade_assessment(ls, action="resume")
    assert ls["assessment"]["status"] == "pending"
    with pytest.raises(ValueError):
        grade_assessment(ls, [0])
    assert ls["node_state"]["a"]["attempts"] == 0


def test_new_coding_package_does_not_use_host_fallback(monkeypatch):
    monkeypatch.setenv("MINDMORPH_TRUSTED_LOCAL_GRADING", "1")
    package = {"origin": "generated", "artifact": {"unit_tests": ["pass"]},
        "task_context": {"format": "coding_challenge", "language": "python"}}
    assert gateway._evaluate(package, "code")["status"] == "needs_evidence"


def test_shared_admission_counts_control_jobs_and_total_worker_memory():
    repo=InMemoryLearningSessionRepository();store=GradingStore(repo)
    store.put_once('a','control:heavy','run',{'state':'accepted','memory_mb':2048})
    heavy,claimed=store.admit_attempt('a','control:heavy');assert claimed
    store.put_once('b','attempt:small','attempt',{'state':'accepted','memory_mb':512})
    waiting,claimed=store.admit_attempt('b','attempt:small')
    assert not claimed and waiting['state']=='queued'
    store.complete_attempt('a','control:heavy',heavy['lease_token'],{'status':'succeeded'})
    running,claimed=store.admit_attempt('b','attempt:small')
    assert claimed and running['state']=='running'


def test_noop_python_test_cannot_award_full_credit():
    from tools.code_executor import execute_tests
    result = execute_tests("", "def test_placeholder():\n    pass")
    assert result["harness_error"] and result.get("score") != 100


def test_standalone_history_survives_reload_without_roadmap(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    exercise = gateway.save_standalone(repo, "u", session()["lessons"]["a"]["exercise"])
    row = GradingStore(repo).get("u", "exercise:" + exercise["id"])
    monkeypatch.setattr(gateway, "_evaluate", lambda *args: valid_result())
    gateway.accept_and_evaluate(repo, "u", row["exercise"], "code", "r1", "standalone", context={"standalone": True})
    saved = gateway.get_standalone(repo, "u", exercise["id"])
    assert saved["attempts"] == 1 and saved["last_result"]["score"] == 100
    assert "node_state" not in saved and repo.list_sessions("u") == []
