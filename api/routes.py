"""HTTP routes for the adaptive learning loop.

Pattern, every mutating endpoint: load the session from the repository → call a ``services`` function
that mutates the dict → save the whole dict back → return it. The repository is resolved per request
(``get_default_repository``), so the store (Postgres / in-memory) is swappable via config.
"""
import json
import logging
import os
import secrets
import uuid

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from api.schemas import (
    AssessmentAnswersRequest,
    ChatRequest,
    CreateSessionRequest,
    FlagRequest,
    FollowUpRequest,
    FollowUpResponse,
    GradeRequest,
    IngestResponse,
    SessionMeta,
    SessionResponse,
    StartSessionResponse,
)
from persistence.repository import get_default_repository
from persistence.errors import StorageUnavailableError, SessionConflictError
from services.events import STAGES, funnel_summary, record_event
from services.learning_service import (
    LockedNodeError,
    StaleExerciseError,
    astream_session,
    build_tutor_messages,
    generate_followup,
    grade,
    grade_assessment,
    open_lesson,
    start_session,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Generation (orchestration / lesson compose / adaptation) is LLM-backed and can fail transiently —
# rate limits (Groq TPM), timeouts, malformed model output. Surface those as a clean 503 with a safe
# message (the real error is logged server-side, never leaked to the client) so the UI can show
# "try again" instead of a raw 500.
GEN_FAILED = "The learning service is busy or rate-limited. Please try again in a moment."


def _service_unavailable(what: str, exc: Exception) -> HTTPException:
    logger.exception("api: %s failed", what)
    return HTTPException(status_code=503, detail=GEN_FAILED)


def _load_or_404(repo, user_id: str, session_id: str) -> dict:
    ls = repo.get(user_id, session_id)
    if ls is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ls


@router.post("/sessions/followup", response_model=FollowUpResponse)
def get_followup_questions(req: FollowUpRequest) -> FollowUpResponse:
    """Generate follow-up questions before creating a session."""
    try:
        result = generate_followup(req.query)
        if not result:
            raise ValueError("Failed to generate follow-up questions")
    except Exception as e:
        raise _service_unavailable("followup", e)
    return FollowUpResponse(**result)


@router.post("/sessions", response_model=StartSessionResponse)
def create_session(req: CreateSessionRequest) -> StartSessionResponse:
    """Run orchestration for a query. Only the SCOUT route yields a persisted learning_session."""
    extra = {}
    if req.preferences is not None:
        extra["preferences"] = req.preferences
    if req.primary_language is not None:
        extra["primary_language"] = req.primary_language
    if req.scope is not None:
        extra["scope"] = req.scope
    if req.domain is not None:
        extra["domain"] = req.domain

    try:
        result = start_session(req.query, req.format_type, **extra)
    except Exception as e:
        raise _service_unavailable("start_session", e)
    resp = StartSessionResponse(
        route=result["route"],
        final_content=result.get("final_content"),
        exercise=result.get("exercise"),
    )
    ls = result.get("learning_session")
    if ls is not None:
        session_id = uuid.uuid4().hex
        get_default_repository().save(req.user_id, session_id, ls, title=req.query)
        resp.session_id = session_id
        resp.learning_session = ls
    return resp


@router.post("/sessions/stream")
async def create_session_stream(req: CreateSessionRequest) -> StreamingResponse:
    """Streaming twin of ``POST /sessions``: SSE progress (one frame per LangGraph node) instead of
    one ~60s blocking wait. Terminal frame carries the same payload shape the sync route returns."""

    extra = {}
    if req.preferences is not None:
        extra["preferences"] = req.preferences
    if req.primary_language is not None:
        extra["primary_language"] = req.primary_language
    if req.scope is not None:
        extra["scope"] = req.scope
    if req.domain is not None:
        extra["domain"] = req.domain

    async def gen():
        try:
            async for evt in astream_session(req.query, req.format_type, **extra):
                if "result" not in evt:
                    yield _sse({"stage": evt["stage"], "label": evt["label"]})
                    continue
                result = evt["result"]
                resp = StartSessionResponse(
                    route=result["route"],
                    final_content=result.get("final_content"),
                    exercise=result.get("exercise"),
                )
                ls = result.get("learning_session")
                if ls is not None:
                    session_id = uuid.uuid4().hex
                    get_default_repository().save(req.user_id, session_id, ls, title=req.query)
                    resp.session_id = session_id
                    resp.learning_session = ls
                yield _sse({"done": True, "session": resp.model_dump()})
        except (StorageUnavailableError, SessionConflictError) as exc:
            yield _sse({"error": str(exc)})
        except Exception:
            logger.exception("api: create_session_stream failed")
            yield _sse({"error": GEN_FAILED})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/sessions/{user_id}", response_model=list[SessionMeta])
def list_sessions(user_id: str) -> list[SessionMeta]:
    return [SessionMeta(**m) for m in get_default_repository().list_sessions(user_id)]


@router.get("/sessions/{user_id}/{session_id}", response_model=SessionResponse)
def get_session(user_id: str, session_id: str) -> SessionResponse:
    repo = get_default_repository()
    ls = _load_or_404(repo, user_id, session_id)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.post("/sessions/{user_id}/{session_id}/lessons/{node_id}", response_model=SessionResponse)
def open_node_lesson(user_id: str, session_id: str, node_id: str) -> SessionResponse:
    repo = get_default_repository()
    ls = _load_or_404(repo, user_id, session_id)
    try:
        ls = open_lesson(ls, node_id, user_id=user_id)
    except LockedNodeError as e:
        # Server-side gate: a locked node cannot be opened even by a hand-crafted request.
        repo.save(user_id, session_id, ls)  # preserve any recovered remediation before returning the lock
        raise HTTPException(status_code=409, detail={"error": "locked", "pending": e.pending})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Lesson compose is LLM-backed (content + exercise) — transient failure ⇒ 503, not 500.
        # Record the failed attempt (a funnel/content signal) and persist before surfacing the 503.
        record_event(ls, STAGES.COMPOSE_FAILURE, node_id=node_id, error=type(e).__name__)
        repo.save(user_id, session_id, ls)
        raise _service_unavailable("open_lesson", e)
    repo.save(user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.post("/users/{user_id}/knowledge", response_model=IngestResponse)
async def ingest_material(user_id: str, file: UploadFile = File(...)) -> IngestResponse:
    """Ingest a user's PDF into their personal RAG store; its chunks ground future lessons (P2 #9)."""
    name = file.filename or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    from rag import registry

    try:
        chunks = registry.ingest_pdf(user_id, data, name)
    except ValueError as e:
        # Unreadable / image-only PDF — client error, not a server fault.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _service_unavailable("ingest_material", e)
    return IngestResponse(filename=name, chunks=chunks)


@router.post("/sessions/{user_id}/{session_id}/assessment", response_model=SessionResponse)
def grade_session_assessment(
    user_id: str, session_id: str, req: AssessmentAnswersRequest
) -> SessionResponse:
    """Grade the onboarding diagnostic quiz; correct answers pre-seed mastered nodes (P2 #8)."""
    repo = get_default_repository()
    ls = _load_or_404(repo, user_id, session_id)
    try:
        ls = grade_assessment(ls, req.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _service_unavailable("grade_assessment", e)
    repo.save(user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/sessions/{user_id}/{session_id}/chat")
async def chat(user_id: str, session_id: str, req: ChatRequest) -> StreamingResponse:
    """Stream a grounded teaching-assistant reply (SSE) and persist the turn (P3 #10)."""
    repo = get_default_repository()
    ls = _load_or_404(repo, user_id, session_id)
    # setdefault: sessions created before this feature have no `chat` key.
    ls.setdefault("chat", [])
    ls["chat"].append({"role": "user", "content": req.message})
    repo.save(user_id, session_id, ls)  # persist the question before streaming (survives a dropped stream)

    messages = build_tutor_messages(ls, req.node_id, req.message, user_id)
    from services.learning_service import _get_tutor_agent

    async def gen():
        full = ""
        try:
            async for token in _get_tutor_agent().astream(messages):
                full += token
                yield _sse({"token": token})
        except Exception:
            logger.exception("api: chat stream failed")
            yield _sse({"error": GEN_FAILED})
            return
        ls["chat"].append({"role": "assistant", "content": full})
        try:
            repo.save(user_id, session_id, ls)
        except (StorageUnavailableError, SessionConflictError) as exc:
            yield _sse({"error": str(exc)})
            return
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{user_id}/{session_id}/grade", response_model=SessionResponse)
def grade_node(user_id: str, session_id: str, node_id: str, req: GradeRequest) -> SessionResponse:
    """Grade a submission, capture mastery, and run adaptation; ``node_id`` is a query param."""
    repo = get_default_repository()
    ls = _load_or_404(repo, user_id, session_id)
    new_node_ids: list[str] = []
    result: dict | None = None
    try:
        extra = {"exercise_revision": req.exercise_revision} if req.exercise_revision is not None else {}
        ls, new_node_ids, result = grade(ls, node_id, req.solution, **extra)
    except LockedNodeError as e:
        raise HTTPException(status_code=409, detail={"error": "locked", "pending": e.pending})
    except StaleExerciseError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Grading itself is deterministic, but adaptation/regeneration is LLM-backed — guard anyway.
        record_event(ls, STAGES.GRADE_FAILURE, node_id=node_id, error=type(e).__name__)
        repo.save(user_id, session_id, ls)
        raise _service_unavailable("grade", e)
    repo.save(user_id, session_id, ls)
    return SessionResponse(
        session_id=session_id,
        learning_session=ls,
        new_node_ids=new_node_ids,
        grade_result=result,
    )


@router.post("/sessions/{user_id}/{session_id}/lessons/{node_id}/flag", response_model=SessionResponse)
def flag_lesson(
    user_id: str, session_id: str, node_id: str, req: FlagRequest
) -> SessionResponse:
    """Record an explicit learner 'this lesson was off' flag — the direct content-quality signal."""
    repo = get_default_repository()
    ls = _load_or_404(repo, user_id, session_id)
    record_event(ls, STAGES.CONTENT_FLAGGED, node_id=node_id, reason=req.reason)
    repo.save(user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.get("/admin/funnel")
def admin_funnel(x_admin_token: str | None = Header(default=None)) -> dict:
    """Aggregate the Gate-1 funnel across every session (operator view).

    Loads full session blobs across all users → shared-secret gated via the ``x-admin-token`` header
    against ``MINDMORPH_ADMIN_TOKEN``. Returns 503 if the token isn't configured (fail closed), 403 on
    mismatch. Real RBAC lands with auth in P3 #13.
    """
    expected = os.environ.get("MINDMORPH_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin funnel disabled (MINDMORPH_ADMIN_TOKEN unset).")
    # Constant-time compare: `!=` short-circuits and leaks the token byte-by-byte under timing analysis.
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")

    sessions = get_default_repository().list_all()
    summaries = [funnel_summary(ls) for _, _, ls in sessions]
    stage_reach: dict[str, int] = {}
    for s in summaries:
        for stage in s["stage_counts"]:
            stage_reach[stage] = stage_reach.get(stage, 0) + 1
    return {
        "total_sessions": len(summaries),
        "reached_grade": sum(1 for s in summaries if s["reached_grade"]),
        "completed": sum(1 for s in summaries if s["completed"]),
        "sessions_reaching_stage": stage_reach,  # how many sessions hit each stage (the funnel)
        "low_score_total": sum(s["low_score_count"] for s in summaries),
        "regenerations_total": sum(s["regenerations"] for s in summaries),
        "content_flags_total": sum(s["content_flags"] for s in summaries),
        "failures_total": sum(s["failures"] for s in summaries),
    }
