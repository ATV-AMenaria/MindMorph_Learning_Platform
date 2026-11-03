"""Validate assessment evidence before any progress consumer sees a score.

Adapters are trusted application code, not learner stdout or a model's claimed total.
The returned dictionaries retain legacy display fields while giving every consumer
the same status/eligibility contract. Calibration is a server policy argument.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GradeStatus = Literal["graded", "feedback_only", "needs_evidence", "pending",
                      "retryable_error", "platform_error", "invalid_submission"]
_STATUSES = set(GradeStatus.__args__)
_METHODS = {"execution", "structured", "rubric", "hybrid"}


class GradeResult(BaseModel):
    """Public normalized envelope; optional legacy adapter fields remain compatible."""

    model_config = ConfigDict(extra="allow")
    status: GradeStatus
    method: Literal["execution", "structured", "rubric", "hybrid"]
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    eligible: bool = False
    mastery_eligible: bool = False
    feedback: str = ""


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Points must be finite numbers.")
    return float(value)


def _points(items: Any) -> float:
    if not isinstance(items, list) or not items or len(items) > 1000:
        raise ValueError("Assessment requires nonempty criterion/check evidence.")
    identifiers, maximum, earned = set(), 0.0, 0.0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Criterion/check evidence must be an object.")
        identifier = item.get("criterion_id", item.get("id"))
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError("Criterion/check IDs must be nonempty and unique.")
        identifiers.add(identifier)
        bound, points = _number(item.get("max_points")), _number(item.get("earned_points"))
        if bound <= 0 or not 0 <= points <= bound:
            raise ValueError("Criterion/check points exceed their published bounds.")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(
                not isinstance(span, str) or not span.strip() for span in evidence):
            raise ValueError("Every criterion/check requires concrete evidence.")
        maximum += bound
        earned += points
    if not math.isclose(maximum, 100.0, abs_tol=1e-8):
        raise ValueError("Published criterion/check maxima must sum to exactly 100.")
    return earned


def normalize_grade_result(result: Any, *, method: str = "execution", calibrated: bool = False) -> dict:
    """Return a safe typed outcome, never inventing a score for absent evidence.

    Legacy execution counts are accepted only when complete, nonempty and internally
    consistent. Structured/rubric totals are computed from bounded point outcomes.
    The caller must pass calibration from its server-owned evaluator policy.
    """
    if method not in _METHODS:
        method = "execution"
    raw = dict(result) if isinstance(result, dict) else {}
    from services.public_projection import RESULT_FIELDS
    out = {**{k: v for k, v in raw.items() if k in RESULT_FIELDS and k != "calibration_verified"},
           "method": method, "score": None, "eligible": False, "mastery_eligible": False}
    out["feedback"] = str(raw.get("feedback") or "")[:8000]

    def invalid(reason: str) -> dict:
        return {**out, "status": "platform_error", "score": None, "eligible": False, "mastery_eligible": False,
                "harness_error": True, "failures": [reason], "error_code": "invalid_grade_result"}

    if not isinstance(result, dict) or not result:
        return invalid("No complete assessment result is available.")
    status = raw.get("status", "graded")
    if status not in _STATUSES:
        return invalid("The assessment returned an unknown status.")
    if raw.get("harness_error"):
        out["status"] = "platform_error"
        return out
    if status != "graded":
        out["status"] = status
        return out
    try:
        if raw.get("complete") is False or raw.get("timed_out"):
            raise ValueError("The assessment did not complete; no final score is available.")
        if any(raw.get(key) for key in ("skipped", "todo", "cancelled")):
            raise ValueError("Skipped or cancelled checks cannot form a complete assessment.")
        if method == "execution":
            passed, total = raw.get("passed"), raw.get("total")
            if type(passed) is not int or type(total) is not int or not 0 <= passed <= total or not 0 < total <= 10000:
                raise ValueError("Execution requires nonempty, consistent completed check counts.")
            failures = raw.get("failures", [])
            if not isinstance(failures, list) or any(not isinstance(item, str) for item in failures):
                raise ValueError("Execution failure details are invalid.")
            if (passed == total and failures) or (passed < total and not failures):
                raise ValueError("Execution check counts disagree with failure evidence.")
            score = passed / total * 100.0
        else:
            score = _points(raw.get("criteria", raw.get("checks")))
            if method == "hybrid" and raw.get("execution_verified") is not True:
                raise ValueError("Hybrid assessment is missing its required execution evidence.")
        claimed = raw.get("score")
        if claimed is not None and (not 0 <= _number(claimed) <= 100 or not math.isclose(float(claimed), score, abs_tol=1e-6)):
            raise ValueError("The claimed total disagrees with validated assessment evidence.")
    except (ValueError, TypeError, OverflowError) as exc:
        return invalid(str(exc))
    if method in {"rubric", "hybrid"} and not calibrated:
        out.update(status="feedback_only", calibration_verified=False)
        return out
    allowed = raw.get("mastery_eligible", raw.get("eligible", True)) is True
    out.update(status="graded", score=score, eligible=allowed, mastery_eligible=allowed)
    if method in {"rubric", "hybrid"}:
        out["calibration_verified"] = True
    if not out["eligible"]:
        out.update(status="feedback_only", score=None)
    return GradeResult.model_validate(out).model_dump()


def eligible_score(result: Any) -> float | None:
    """Return only a revalidated progress-eligible score, including legacy counts."""
    method = result.get("method", "execution") if isinstance(result, dict) else "execution"
    # No rubric category has independent calibration approval yet. A caller/model
    # cannot turn a JSON flag into server policy.
    normalized = normalize_grade_result(result, method=method, calibrated=False)
    return normalized["score"] if normalized["status"] == "graded" and normalized["eligible"] else None
