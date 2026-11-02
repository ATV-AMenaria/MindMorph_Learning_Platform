"""Request/response models for the API boundary.

The ``learning_session`` itself is passed through as an open dict (``data``) — it is the persisted blob
and its shape is owned by the services layer, not re-declared here (which would drift). These models
only type the thin request envelopes and list metadata.
"""
from typing import Any, Optional, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator
from services.learner_profile import make_profile


class CreateSessionRequest(BaseModel):
    _creation_token: str | None = PrivateAttr(default=None)
    user_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    format_type: str = "B"
    preferences: Optional[dict] = None
    primary_language: Optional[str] = None
    scope: Optional[str] = None
    domain: Optional[str] = None
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def normalize_profile(self):
        profile = make_profile(self.preferences, self.primary_language, self.scope, self.domain, self.query)
        self.domain = profile.get("domain")
        self.primary_language = profile.get("primary_language")
        self.scope = profile.get("scope")
        return self


class FollowUpRequest(BaseModel):
    query: str = Field(..., min_length=1)


class FollowUpResponse(BaseModel):
    questions: list[dict]
    detected_domain: str
    detected_language: Optional[str] = None
    detected_specialization: Optional[str] = None


class GradeRequest(BaseModel):
    solution: str = Field(max_length=262144)
    exercise_revision: Optional[str] = None
    attempt_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class RunRequest(BaseModel):
    solution: str = Field(max_length=262144)
    stdin: str = Field(default="", max_length=262144)
    exercise_revision: Optional[str] = None
    run_id: str = Field(min_length=1, max_length=128)


class NewPracticeRequest(BaseModel):
    exercise_revision: Optional[str] = None
    request_id: str = Field(min_length=1, max_length=128)


class IngestResponse(BaseModel):
    filename: str
    chunks: int


class AssessmentAnswersRequest(BaseModel):
    answers: Optional[list[int]] = None
    action: Literal["submit", "skip", "resume"] = "submit"
    quiz_revision: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    node_id: Optional[str] = None  # the open skill node, for grounding (optional)


class FlagRequest(BaseModel):
    # Optional "what was off" note. UNTRUSTED learner text — capped + whitespace-stripped here, but it
    # is stored verbatim in the session events; any UI that renders it MUST HTML-escape (stored-XSS).
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class SessionMeta(BaseModel):
    session_id: str
    title: str
    updated_at: Optional[str] = None


class StartSessionResponse(BaseModel):
    route: str
    session_id: Optional[str] = None
    learning_session: Optional[dict[str, Any]] = None
    final_content: Optional[str] = None
    exercise: Optional[dict[str, Any]] = None
    grounding: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def project_public(self):
        from services.public_projection import public_session, public_exercise
        if self.learning_session is not None:
            self.learning_session = public_session(self.learning_session)
        self.exercise = public_exercise(self.exercise)
        return self


class SessionResponse(BaseModel):
    session_id: str
    learning_session: dict[str, Any]
    # Ids of any remedial/unlock nodes an adaptation just added to the graph (empty for every response
    # except the grade call that triggered it) — lets the client animate the rewire instead of it
    # looking like a silent snap to a new layout.
    new_node_ids: list[str] = Field(default_factory=list)
    # This submission's grade dict (grade responses only). Sent separately from
    # node_state.last_feedback because adaptation clears that field on the remediation path, and the
    # learner still has to be able to read the score and feedback that failed them.
    grade_result: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def project_public(self):
        from services.public_projection import public_session, public_result
        self.learning_session = public_session(self.learning_session)
        self.grade_result = public_result(self.grade_result)
        return self
