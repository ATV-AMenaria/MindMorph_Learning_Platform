"""Phase 2 — mastery capture: score → node_state via app._apply_score (pure, no Streamlit run)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from services.mastery import apply_score as _apply_score, MASTERY_THRESHOLD, REVIEW_THRESHOLD


def _ls(node_id="n1", **overrides):
    base = {"status": "available", "best_score": 0, "attempts": 0, "weaknesses": [], "last_feedback": None}
    base.update(overrides)
    return {"node_state": {node_id: base}}


def test_high_score_marks_mastered():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 100}))
    ns = ls["node_state"]["n1"]
    assert ns["status"] == "mastered"
    assert ns["best_score"] == 100
    assert ns["attempts"] == 1


def test_mid_score_is_in_progress():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 65}))
    assert ls["node_state"]["n1"]["status"] == "in_progress"


def test_low_score_needs_review():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 30}))
    assert ls["node_state"]["n1"]["status"] == "needs_review"


def test_threshold_boundaries():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": MASTERY_THRESHOLD}))
    assert ls["node_state"]["n1"]["status"] == "mastered"
    ls = _ls()  # fresh node: sticky mastery would otherwise pin the score below
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": REVIEW_THRESHOLD}))
    assert ls["node_state"]["n1"]["status"] == "in_progress"


def test_regrade_keeps_best_score_and_increments_attempts():
    ls = _ls(best_score=90, attempts=1, status="mastered")
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 40}))
    ns = ls["node_state"]["n1"]
    assert ns["best_score"] == 90      # max(old, new) preserved
    assert ns["attempts"] == 2
    assert ns["status"] == "mastered"  # mastery is sticky: a worse retry does not revoke it


def test_mastery_is_sticky_across_attempts():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 100}))  # earns mastery
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 10}))   # worse retry
    assert ls["node_state"]["n1"]["status"] == "mastered"
    assert ls["node_state"]["n1"]["best_score"] == 100


def test_struggle_still_shows_when_never_mastered():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 65}))  # in_progress
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 20}))  # regress
    assert ls["node_state"]["n1"]["status"] == "needs_review"


def test_last_feedback_stores_result():
    ls = _ls()
    result = _evidence({"score": 100, "passed": 3, "total": 3})
    _apply_score(ls, "n1", "coding_challenge", result)
    assert ls["node_state"]["n1"]["last_feedback"] == result


def test_case_study_unverified_total_does_not_award_mastery():
    ls = _ls()
    _apply_score(ls, "n1", "case_study", {"score": 85, "method": "rubric", "status": "feedback_only"})
    assert ls["node_state"]["n1"]["status"] == "available"
    assert ls["node_state"]["n1"]["attempts"] == 0


def test_missing_score_leaves_progress_unchanged():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", {})
    assert ls["node_state"]["n1"]["status"] == "available"
    assert ls["node_state"]["n1"]["best_score"] == 0
    assert ls["node_state"]["n1"]["attempts"] == 0


# --- Deterministic remediation lock (sub-40) ---------------------------------------------------

def test_sub40_sets_remediation_pending():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 30}))
    assert ls["node_state"]["n1"]["remediation_pending"] is True


def test_40_boundary_does_not_flag():
    # 40 is the floor of "keep practicing" (in_progress) — NOT remediation.
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": REVIEW_THRESHOLD}))
    assert ls["node_state"]["n1"]["status"] == "in_progress"
    assert ls["node_state"]["n1"]["remediation_pending"] is False


def test_ge40_grade_clears_remediation_pending():
    ls = _ls(remediation_pending=True, status="needs_review")
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 55}))
    assert ls["node_state"]["n1"]["remediation_pending"] is False


def test_mastered_node_never_flagged_even_on_worse_retry():
    ls = _ls()
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 100}))  # sticky mastered
    _apply_score(ls, "n1", "coding_challenge", _evidence({"score": 20}))   # worse retry, still mastered
    assert ls["node_state"]["n1"]["status"] == "mastered"
    assert ls["node_state"]["n1"]["remediation_pending"] is False


def _evidence(raw):
    """Completed check evidence for state/timing tests; not a model total."""
    score = float(raw["score"])
    return {**raw, "method": "execution", "passed": round(score * 100), "total": 10000,
            "failures": [] if score == 100 else ["Fixture learner check failed"]}
