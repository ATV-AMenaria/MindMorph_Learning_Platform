"""Explicit learner DTOs. Private package material never crosses this boundary."""
from copy import deepcopy

RESULT_FIELDS = {"status", "method", "score", "eligible", "mastery_eligible", "feedback",
    "passed", "total", "failures", "timed_out", "harness_error", "error_code", "criteria",
    "per_criterion", "attempt_id", "applied", "application_status", "retryable", "availability",
    "duration_ms", "calibration_verified"}
EXERCISE_FIELDS = {"id", "revision", "format", "language", "statement", "grounding", "instructions",
    "rubric", "status", "availability", "execution_profile", "profile_revision", "evaluator_revision",
    "last_result", "grade_result", "attempts", "history", "created_at", "title", "practice_mode"}
SESSION_FIELDS = {"session_id", "skill_graph", "skill_graph_mermaid", "summary", "review_passed",
    "review_notes", "academic_output", "market_output", "practical_output", "format_type", "grounding",
    "graph_identity", "public_query", "node_state", "lessons", "selected_node", "chat", "usage", "events",
    "canonical", "learner_profile", "preferences", "primary_language", "scope", "domain", "timing", "assessment"}


def public_result(result):
    if not isinstance(result, dict):
        return None
    out = {k: deepcopy(v) for k, v in result.items() if k in RESULT_FIELDS}
    # Evidence may include private expected values; learner feedback only exposes their
    # own quoted rubric excerpts and criterion bounds, never executable oracles.
    if "criteria" in out:
        out["criteria"] = [{k: deepcopy(v) for k, v in c.items()
            if k in {"criterion_id", "max_points", "earned_points", "evidence"}}
            for c in out["criteria"] if isinstance(c, dict)]
    return out


def public_exercise(exercise):
    if not isinstance(exercise, dict):
        return None
    out = {k: deepcopy(v) for k, v in exercise.items() if k in EXERCISE_FIELDS}
    from services.knowledge_tracking import enabled
    if not enabled() or out.get('practice_mode') not in {'foundation', 'standard', 'challenge'}:
        out.pop('practice_mode', None)
    for key in ("last_result", "grade_result"):
        if key in out:
            out[key] = public_result(out[key])
    if "history" in out:
        out["history"] = [public_result(v) for v in out["history"]]
    return out


def public_session(ls):
    from services.knowledge_tracking import public_summary
    out = {k: deepcopy(v) for k, v in ls.items() if k in SESSION_FIELDS}
    out["lessons"] = {node: {k: deepcopy(v) for k, v in lesson.items()
        if k in {"content", "grounding", "usage", "timing"}} | {"exercise": public_exercise(lesson.get("exercise"))}
        for node, lesson in ls.get("lessons", {}).items()}
    for state in out.get("node_state", {}).values():
        summary = public_summary(state.get('knowledge_tracking'))
        if summary is None:
            state.pop('knowledge_tracking', None)
        else:
            state['knowledge_tracking'] = summary
        if "last_feedback" in state:
            state["last_feedback"] = public_result(state["last_feedback"])
    assessment = ls.get("assessment")
    if isinstance(assessment, dict):
        submitted = assessment.get("submitted") is True or assessment.get("status") == "submitted"
        projected = {k: deepcopy(v) for k, v in assessment.items()
            if k in {"revision", "status", "submitted"} or (submitted and k == "results")}
        projected.setdefault("status", "submitted" if submitted else "pending")
        projected["quiz_revision"] = assessment.get("revision")
        projected["quiz"] = {"questions": [{k: deepcopy(v) for k, v in q.items()
            if k in {"id", "node_id", "question", "options"} or
                (submitted and k in {"correct_index", "explanation"})}
            for q in assessment.get("quiz", {}).get("questions", [])]}
        out["assessment"] = projected
    return out
