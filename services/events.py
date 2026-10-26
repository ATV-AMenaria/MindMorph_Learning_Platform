"""Funnel instrumentation — an append-only event timeline on the learning_session (Gate-1 observability).

`COMMERCIALIZATION.md` Gate 1 needs to see *where learners drop off* and *where generated content is
wrong*. Nothing recorded the journey before this — only `ls["usage"]` (cost) and `ls["node_state"]`
(current mastery). Here every loop action appends one structured event to `ls["events"]`; the timeline
is the single source of truth and `funnel_summary` derives the funnel/drop-off/content-quality
aggregates on read. Persists for free in the JSONB session blob (same path as `usage`).

Mirrors the cost pattern (`services/cost.py`): record raw at one chokepoint per stage, aggregate on read.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from services.mastery import REVIEW_THRESHOLD
from services.telemetry import emit_telemetry

logger = logging.getLogger(__name__)


class STAGES:
    """Event stage names. Constants (not magic strings) so writer and reader can't drift."""

    SESSION_CREATED = "session_created"
    ASSESSMENT_SUBMITTED = "assessment_submitted"
    LESSON_OPENED = "lesson_opened"
    EXERCISE_GRADED = "exercise_graded"
    NODE_MASTERED = "node_mastered"
    NODE_NEEDS_REVIEW = "node_needs_review"
    LESSON_REGENERATED = "lesson_regenerated"
    PATH_COMPLETED = "path_completed"
    COMPOSE_FAILURE = "compose_failure"
    GRADE_FAILURE = "grade_failure"
    CONTENT_FLAGGED = "content_flagged"


def record_event(ls: dict, stage: str, **fields: Any) -> None:
    """Append one event to the session timeline and stream to telemetry/Kafka."""
    event = {"ts": datetime.now(timezone.utc).isoformat(), "stage": stage, **fields}
    ls.setdefault("events", []).append(event)
    try:
        emit_telemetry(event)
    except Exception as e:
        logger.debug("Telemetry emission skipped or failed (non-fatal): %s", e)


def has_event(ls: dict, stage: str) -> bool:
    """True if any event of ``stage`` was already recorded (used to fire once-only events).

    Linear scan — fine for short per-session timelines; revisit with an index if event lists grow large.
    """
    return any(e.get("stage") == stage for e in ls.get("events", []))


def funnel_summary(ls: dict) -> dict:
    """Derive funnel + content-quality aggregates from the event timeline.

    Everything here is computed from ``ls["events"]`` — the timeline is the single source of truth, so a
    field added/renamed in ``record_event`` flows through without a second place to update.
    """
    events = ls.get("events", [])
    stage_counts: dict[str, int] = {}
    scores: list[float] = []
    opened_nodes: set[str] = set()
    flagged_nodes: list[str] = []
    for e in events:
        stage = e.get("stage")
        if stage is None:  # malformed event (not via record_event) — don't pollute the aggregate
            continue
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        if stage == STAGES.LESSON_OPENED and e.get("node_id") is not None:
            opened_nodes.add(e["node_id"])
        elif stage == STAGES.EXERCISE_GRADED and e.get("score") is not None:
            scores.append(float(e["score"]))
        elif stage == STAGES.CONTENT_FLAGGED and e.get("node_id") is not None:
            flagged_nodes.append(e["node_id"])

    low_scores = sum(1 for s in scores if s < REVIEW_THRESHOLD)
    last = events[-1] if events else None
    return {
        "stage_counts": stage_counts,
        "distinct_nodes_opened": len(opened_nodes),
        "scores": scores,
        "low_score_count": low_scores,  # graded < REVIEW_THRESHOLD — proxy for "content may be wrong"
        "regenerations": stage_counts.get(STAGES.LESSON_REGENERATED, 0),
        "content_flags": len(flagged_nodes),  # explicit learner thumbs-down
        "flagged_nodes": flagged_nodes,
        "failures": stage_counts.get(STAGES.COMPOSE_FAILURE, 0)
        + stage_counts.get(STAGES.GRADE_FAILURE, 0),
        "reached_grade": STAGES.EXERCISE_GRADED in stage_counts,
        "completed": STAGES.PATH_COMPLETED in stage_counts,
        "last_stage": last["stage"] if last else None,  # the drop point
        "last_ts": last["ts"] if last else None,
        "event_count": len(events),
    }
