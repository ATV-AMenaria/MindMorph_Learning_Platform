"""Dynamic skill assessment (P2 #8): quiz generation hookup + grading that pre-seeds mastery.

The LLM agent is mocked; grading is exercised against a real ``learning_session`` shape with the quiz
round-tripped through JSON (proves dict-key access on the persisted blob)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest

import services.learning_service as svc
from agents.assessment.assessment_schema import AssessmentQuiz, MCQQuestion


def _scout_state():
    return {
        "skill_graph": {
            "summary": "Test path",
            "nodes": [
                {"id": "a", "label": "A", "description": "node a"},
                {"id": "b", "label": "B", "description": "node b"},
            ],
            "edges": [],
        }
    }


def _quiz(*items) -> AssessmentQuiz:
    return AssessmentQuiz(
        questions=[
            MCQQuestion(node_id=nid, question=f"Question for {nid}", options=["w", "x", "y", "z"], correct_index=ci)
            for nid, ci in items
        ]
    )


# --- quiz attached at session creation ----------------------------------------------------------

def test_new_session_attaches_assessment(monkeypatch):
    monkeypatch.setattr(svc, "_get_assessment_agent", lambda: _FakeAgent(_quiz(("a", 1), ("b", 2))))
    ls = svc.new_learning_session(_scout_state(), "B")
    assert ls["assessment"]["submitted"] is False
    assert len(ls["assessment"]["quiz"]["questions"]) == 2


def test_assessment_failure_is_graceful(monkeypatch):
    # Agent raises → no assessment key, session still created.
    monkeypatch.setattr(svc, "_get_assessment_agent", lambda: _BoomAgent())
    ls = svc.new_learning_session(_scout_state(), "B")
    assert ls is not None and "assessment" not in ls


class _FakeAgent:
    def __init__(self, quiz):
        self._quiz = quiz

    def assess(self, _json):
        return self._quiz


class _BoomAgent:
    def assess(self, _json):
        raise RuntimeError("llm down")


# --- grading ------------------------------------------------------------------------------------

def _session_with_quiz(quiz: AssessmentQuiz) -> dict:
    # Minimal ls, round-tripped through JSON like the persisted store (proves dict-key grading).
    base = {
        "skill_graph": _scout_state()["skill_graph"],
        "node_state": {"a": _default(), "b": _default()},
        "assessment": {"quiz": quiz.model_dump(), "submitted": False},
    }
    return json.loads(json.dumps(base))


def _default():
    from services.mastery import default_node_state

    return default_node_state()


def test_grade_marks_correct_nodes_in_progress_not_mastered():
    from services.mastery import MASTERY_THRESHOLD

    ls = _session_with_quiz(_quiz(("a", 1), ("b", 0)))
    svc.grade_assessment(ls, [1, 3])  # a correct, b wrong
    # A correct MCQ is a head-start (in_progress), NOT full mastery — acing the quiz must not empty the path.
    assert ls["node_state"]["a"]["status"] == "in_progress"
    assert ls["node_state"]["a"]["best_score"] == svc.ASSESSMENT_PASS_SCORE < MASTERY_THRESHOLD
    assert ls["node_state"]["b"]["status"] == "available"  # wrong → untouched
    assert ls["assessment"]["submitted"] is True


def test_all_skipped_marks_nothing():
    ls = _session_with_quiz(_quiz(("a", 1), ("b", 0)))
    svc.grade_assessment(ls, [-1, -1])
    assert ls["node_state"]["a"]["status"] == "available"
    assert ls["node_state"]["b"]["status"] == "available"


def test_length_mismatch_raises():
    ls = _session_with_quiz(_quiz(("a", 1)))
    with pytest.raises(ValueError):
        svc.grade_assessment(ls, [1, 0])


def test_hallucinated_node_id_rejects_quiz_without_phantom_progress():
    # Correct answer but node_id not in the graph → must be skipped, no phantom node_state entry.
    ls = _session_with_quiz(_quiz(("ghost", 1), ("a", 0)))
    with pytest.raises(ValueError):
        svc.grade_assessment(ls, [1, 0])
    assert "ghost" not in ls["node_state"]            # no phantom
    assert set(ls["node_state"]) == {"a", "b"}        # counter stays sound
    assert ls["node_state"]["a"]["status"] == "available"


def test_no_quiz_raises():
    with pytest.raises(ValueError):
        svc.grade_assessment({"node_state": {}}, [])
