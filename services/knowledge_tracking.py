"""Experimental, session-scoped BKT recommendations; never a progression gate.

Only the grading transaction creates observations. A code test is not a learning
opportunity: the first eligible submission to a distinct exercise is one outcome.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)
MODEL_VERSION = "bkt-v1"
MIN_OBSERVATIONS = 3
PRIOR, LEARN, GUESS, SLIP = 0.2, 0.1, 0.2, 0.1
Mode = Literal["foundation", "standard", "challenge"]


def enabled() -> bool:
    return os.getenv("MINDMORPH_BKT", "0") == "1"


class PracticeGuidance(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Mode = "standard"
    reason: str = Field(max_length=500)


class KnowledgeSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["insufficient_evidence", "ready", "unavailable"]
    probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    observation_count: int = Field(ge=0)
    model_version: str = MODEL_VERSION
    recommendation: PracticeGuidance

    @model_validator(mode="after")
    def evidence_bounds(self):
        if self.status == "ready":
            if self.observation_count < MIN_OBSERVATIONS or self.probability is None:
                raise ValueError("A ready estimate requires three observations and a probability")
        elif self.probability is not None:
            raise ValueError("Unavailable/insufficient estimates must not display a probability")
        return self


def recommendation(probability: float, count: int) -> PracticeGuidance:
    if count < MIN_OBSERVATIONS:
        return PracticeGuidance(mode="standard", reason="Complete more new exercises to build your learning estimate.")
    if probability < 0.6:
        return PracticeGuidance(mode="foundation", reason="Strengthen this skill with a smaller problem and a worked example.")
    if probability < 0.9:
        return PracticeGuidance(mode="standard", reason="Keep practicing this skill with a new variation.")
    return PracticeGuidance(mode="challenge", reason="Try applying this skill in a more challenging problem.")


def predict_history(outcomes: list[bool]) -> list[float]:
    """Return post-observation, post-learning latent state probabilities.

    pyBKT's one-step outputs precede each observation. Append an UNOBSERVED
    opportunity (0 in its low-level encoding) to obtain the last updated state.
    Incorrect/correct are 1/2 here, not the high-level DataFrame's 0/1 encoding.
    No fitting, sampling, network requests or multiprocessing occurs.
    """
    if not outcomes:
        return []
    if any(type(value) is not bool for value in outcomes):
        raise ValueError("BKT requires boolean exercise outcomes")
    import numpy as np
    from pyBKT.fit import predict_onestep

    size = len(outcomes) + 1
    model = {"prior": PRIOR, "learns": np.array([LEARN]), "forgets": np.array([0.0]),
             "guesses": np.array([GUESS]), "slips": np.array([SLIP])}
    data = {"data": np.array([[2 if value else 1 for value in outcomes] + [0]], dtype=np.int32),
            "resources": np.ones(size, dtype=np.int64), "starts": np.array([1], dtype=np.int64),
            "lengths": np.array([size], dtype=np.int64)}
    _, states = predict_onestep.run(model, data, parallel=False)
    probabilities = [float(value) for value in states[1, 1:]]
    if len(probabilities) != len(outcomes) or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("BKT produced an invalid state estimate")
    return probabilities


def summarize(observations: list[dict]) -> dict:
    """Replay durable evidence, including observations saved during a model outage."""
    started = time.monotonic()
    count = len(observations)
    try:
        outcomes = [row["successful"] for row in observations]
        probabilities = predict_history(outcomes)
        probability = probabilities[-1] if probabilities else PRIOR
        result = KnowledgeSummary(status="ready" if count >= MIN_OBSERVATIONS else "insufficient_evidence",
            probability=probability if count >= MIN_OBSERVATIONS else None,
            observation_count=count, recommendation=recommendation(probability, count))
    except Exception as error:
        # Model/package failures must not turn a valid grade into a learner failure.
        logger.warning("knowledge_tracking: prediction unavailable error_type=%s observations=%d", type(error).__name__, count)
        result = KnowledgeSummary(status="unavailable", observation_count=count,
            recommendation=PracticeGuidance(mode="standard", reason="Learning recommendations are temporarily unavailable. Continue with standard practice."))
    logger.info("knowledge_tracking: observations=%d status=%s mode=%s duration_ms=%.2f", count,
                result.status, result.recommendation.mode, (time.monotonic() - started) * 1000)
    return result.model_dump()


def public_summary(value: dict | None) -> dict | None:
    if not enabled():
        return None
    if value is None:
        return KnowledgeSummary(status="insufficient_evidence", observation_count=0,
            recommendation=recommendation(PRIOR, 0)).model_dump()
    try:
        return KnowledgeSummary.model_validate(value).model_dump()
    except (ValueError, TypeError):
        return KnowledgeSummary(status="unavailable", observation_count=0,
            recommendation=PracticeGuidance(mode="standard", reason="Learning recommendations are temporarily unavailable. Continue with standard practice.")).model_dump()


def guidance(value: dict | None) -> dict | None:
    """Server-owned guidance, ignored when the release flag is disabled."""
    if not enabled() or value is None:
        return None
    try:
        return PracticeGuidance.model_validate(value).model_dump()
    except (ValueError, TypeError):
        return None


def guidance_prompt(value: dict | None) -> str:
    checked = guidance(value)
    if checked is None:
        return ""
    directions = {
        "foundation": "Use a smaller problem targeting the current skill. Include a worked example with different inputs; leave the learner's task unsolved.",
        "standard": "Use a new variation at the lesson's difficulty, with different inputs or context.",
        "challenge": "Use a more challenging application of the current skill, without requiring skills beyond this lesson or locked prerequisites.",
    }
    return ("\n\nPractice guidance: " + directions[checked["mode"]] +
            " Preserve the learner's subject, language, lesson evidence and runtime contract. Do not mention internal learning estimates or model settings.")


def observation_key(sid: str, node: str, exercise_id: str) -> str:
    identity = json.dumps([sid, node, exercise_id], separators=(",", ":"))
    return "knowledge:" + hashlib.sha256(identity.encode()).hexdigest()


def attempt_scope(ls: dict, sid: str, attempt: dict) -> str | None:
    """Guard the only evidence writer; Run/standalone/diagnostics cannot enter it."""
    if not enabled() or attempt.get("kind") != "attempt" or attempt.get("state") != "completed":
        return None
    from services.grading_results import eligible_score
    context = attempt.get("context") or {}
    node = context.get("node_id")
    if (context != {"session_id": sid, "node_id": node} or not node
            or node not in ls.get("node_state", {}) or not attempt.get("exercise_id")
            or eligible_score(attempt.get("result")) is None):
        return None
    return node


def track_attempt(ls: dict, sid: str, node: str, attempt: dict, history: list[dict]) -> tuple[str, dict] | None:
    """Prepare state and optional ledger insertion inside the grading transaction."""
    from services.grading_results import eligible_score
    from services.mastery import MASTERY_THRESHOLD

    exercise_id = attempt["exercise_id"]
    pending = None
    if not any(row["exercise_id"] == exercise_id for row in history):
        row = {"kind": "knowledge_observation", "session_id": sid, "node_id": node,
               "exercise_id": exercise_id, "exercise_revision": attempt["revision"],
               "attempt_id": attempt["attempt_id"], "successful": eligible_score(attempt["result"]) >= MASTERY_THRESHOLD,
               "sequence": len(history) + 1, "model_version": MODEL_VERSION,
               "created_at": datetime.now(timezone.utc).isoformat()}
        history = [*history, row]
        pending = observation_key(sid, node, exercise_id), row
    ls["node_state"][node]["knowledge_tracking"] = summarize(history)
    return pending
