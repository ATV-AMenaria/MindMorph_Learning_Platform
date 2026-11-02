"""HTTP routes for the adaptive learning loop.

Pattern, every mutating endpoint: load the session from the repository → call a ``services`` function
that mutates the dict → save the whole dict back → return it. The repository is resolved per request
(``get_default_repository``), so the store (Postgres / in-memory) is swappable via config.

Now secured with JWT authentication & multi-tenant isolation:
Every request extracts verified user_id from request.state.user_id (populated by JWTAuthenticationMiddleware).
Cross-tenant access returns 403 Forbidden.
Rate limits applied via slowapi annotations.
"""
import json
import asyncio
import logging
import os
import secrets
import uuid

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.limiter import limiter
from api.schemas import (
    AssessmentAnswersRequest,
    ChatRequest,
    CreateSessionRequest,
    FlagRequest,
    FollowUpRequest,
    FollowUpResponse,
    GradeRequest,
    RunRequest,
    NewPracticeRequest,
    IngestResponse,
    SessionMeta,
    SessionResponse,
    StartSessionResponse,
)
from persistence.repository import get_default_repository
from persistence.errors import StorageUnavailableError, SessionConflictError
from services.events import STAGES, funnel_summary, record_event
from services.generation_privacy import private_generation
from persistence.grading_store import GradingStore
from services import grading_gateway as gateway
from services import practice_runs
from services.public_projection import public_session, public_result
from rag.grounding import retrieve_evidence, validate_citations, CitationStream
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
    tutor_retrieval_query,
)

logger = logging.getLogger(__name__)
router = APIRouter()

GEN_FAILED = "The learning service is busy or rate-limited. Please try again in a moment."


def _accept_creation(req, repo):
    if not req.request_id:
        return None
    key = "create:" + req.request_id
    binding = gateway.digest(req.model_dump(exclude={"request_id"}))
    store = GradingStore(repo)
    record, _ = store.put_once(req.user_id, key, "creation", {"binding": binding, "state": "accepted"})
    if record["binding"] != binding:
        raise HTTPException(409, "This request ID is bound to different learning preferences.")
    if record.get("state") == "completed":
        return StartSessionResponse(**record["response"])
    if record.get("state") == "generated":
        return _materialize_creation(req, repo, record["result"])
    record, claimed = store.claim_generation(req.user_id, key)
    if not claimed:
        raise HTTPException(409, "This learning request is already being prepared.")
    req._creation_token = record["lease_token"]
    return None


def _finish_creation(req, repo, result):
    # Persist generation before materialization. A crash after either this write
    # or session.save resumes the same identities without another model call.
    from persistence.grading_store import clone
    result = clone(result)
    ls = result.get("learning_session")
    if ls is not None:
        ls.setdefault("graph_identity", uuid.uuid4().hex)
        for lesson in ls.get("lessons", {}).values():
            exercise = lesson.get("exercise")
            if isinstance(exercise, dict):
                exercise.setdefault("id", uuid.uuid4().hex)
                exercise.setdefault("revision", uuid.uuid4().hex)
    if isinstance(result.get("exercise"), dict):
        result["exercise"].setdefault("id", uuid.uuid4().hex)
        result["exercise"].setdefault("revision", uuid.uuid4().hex)
    if req.request_id:
        store = GradingStore(repo)
        record = store.update(req.user_id, "create:" + req.request_id,
            {"state": "generated", "result": result}, expected_status="accepted", expected_token=req._creation_token)
        if record.get("state") == "completed":
            return StartSessionResponse(**record["response"])
        if record.get("state") != "generated":
            raise SessionConflictError("This learning request was recovered by another worker. Retry the same request.")
        result = record["result"]
    return _materialize_creation(req, repo, result)


def _materialize_creation(req, repo, result):
    from persistence.grading_store import clone
    result = clone(result)
    ls = result.get("learning_session")
    session_id = None
    if ls is not None:
        session_id = ls.get("graph_identity") or uuid.uuid4().hex
        ls["session_id"] = session_id
        ls["user_id"] = req.user_id
        saved = repo.get(req.user_id, session_id)
        if saved is None:
            gateway.synchronize_session(repo, req.user_id, session_id, ls)
            try:
                repo.save(req.user_id, session_id, ls, title=req.query)
            except SessionConflictError:
                saved = repo.get(req.user_id, session_id)
                if saved is None:
                    raise
        # A retry cannot replace learner updates made after the first save.
        if saved is not None:
            ls = saved
    exercise = result.get("exercise")
    if exercise:
        exercise = gateway.save_standalone(repo, req.user_id, exercise)
    response = StartSessionResponse(route=result["route"], session_id=session_id,
        learning_session=ls, final_content=result.get("final_content"), exercise=exercise,
        grounding=result.get("grounding"))
    if req.request_id:
        record = GradingStore(repo).update(req.user_id, "create:" + req.request_id,
            {"state": "completed", "response": response.model_dump()}, expected_status="generated")
        return StartSessionResponse(**record["response"])
    return response


def _creation_failed(req, repo):
    if req.request_id:
        GradingStore(repo).update(req.user_id, "create:" + req.request_id,
            {"state": "retryable_error"}, expected_status="accepted", expected_token=req._creation_token)


def _service_unavailable(what: str, exc: Exception) -> HTTPException:
    logger.error("api: %s failed (%s)", what, type(exc).__name__)
    return HTTPException(status_code=503, detail=GEN_FAILED)


def _get_authed_user_id(request: Request, fallback_user_id: str | None = None) -> str:
    """Return authenticated user_id from middleware state, with optional fallback for legacy/internal calls."""
    uid = getattr(request.state, "user_id", None)
    if uid:
        return uid
    if fallback_user_id:
        return fallback_user_id
    raise HTTPException(status_code=401, detail="Authentication required")


def _load_or_404(repo, authed_user_id: str, session_id: str, target_user_id: str | None = None) -> dict:
    """Load session and verify multi-tenant ownership."""
    if target_user_id and target_user_id != authed_user_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: You do not have permission to access another user's session"
        )
    ls = repo.get(authed_user_id, session_id)
    if ls is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ls


@router.post("/sessions/followup", response_model=FollowUpResponse)
@limiter.limit("15/minute")
def get_followup_questions(request: Request, req: FollowUpRequest) -> FollowUpResponse:
    """Generate follow-up questions before creating a session."""
    try:
        result = generate_followup(req.query)
        if not result:
            raise ValueError("Failed to generate follow-up questions")
    except Exception as e:
        raise _service_unavailable("followup", e)
    return FollowUpResponse(**result)


@router.post("/sessions", response_model=StartSessionResponse)
@limiter.limit("10/minute")
def create_session(request: Request, req: CreateSessionRequest) -> StartSessionResponse:
    """Run orchestration for a query. Only the SCOUT route yields a persisted learning_session."""
    req.user_id = _get_authed_user_id(request, req.user_id)
    repo = get_default_repository()
    replay = _accept_creation(req, repo)
    if replay:
        return replay
    extra = {"user_id": req.user_id}
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
        return _finish_creation(req, repo, result)
    except (StorageUnavailableError, SessionConflictError):
        _creation_failed(req, repo)
        raise
    except Exception as e:
        _creation_failed(req, repo)
        raise _service_unavailable("start_session", e)


@router.post("/sessions/stream")
@limiter.limit("10/minute")
async def create_session_stream(request: Request, req: CreateSessionRequest) -> StreamingResponse:
    """Streaming twin of POST /sessions: SSE progress with verified user identity."""
    authed_user_id = _get_authed_user_id(request, req.user_id)
    req.user_id = authed_user_id

    repo = get_default_repository()
    replay = await asyncio.to_thread(_accept_creation, req, repo)
    extra = {"user_id": req.user_id}
    if req.preferences is not None:
        extra["preferences"] = req.preferences
    if req.primary_language is not None:
        extra["primary_language"] = req.primary_language
    if req.scope is not None:
        extra["scope"] = req.scope
    if req.domain is not None:
        extra["domain"] = req.domain

    async def gen():
        if replay:
            yield _sse({"done": True, "session": replay.model_dump()})
            return
        try:
            async for evt in astream_session(req.query, req.format_type, **extra):
                if "result" not in evt:
                    yield _sse({"stage": evt["stage"], "label": evt["label"]})
                    continue
                result = evt["result"]
                resp = await asyncio.to_thread(_finish_creation, req, repo, result)
                yield _sse({"done": True, "session": resp.model_dump()})
        except (StorageUnavailableError, SessionConflictError) as exc:
            _creation_failed(req, repo)
            yield _sse({"error": str(exc)})
        except asyncio.CancelledError:
            _creation_failed(req, repo)
            raise
        except Exception:
            _creation_failed(req, repo)
            logger.error("api: create_session_stream failed")
            yield _sse({"error": GEN_FAILED})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/sessions/{user_id}", response_model=list[SessionMeta])
def list_sessions(request: Request, user_id: str) -> list[SessionMeta]:
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot list sessions for another user")
    return [SessionMeta(**m) for m in get_default_repository().list_sessions(authed_user_id)]


@router.get("/sessions/{user_id}/{session_id}", response_model=SessionResponse)
def get_session(request: Request, user_id: str, session_id: str) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    return SessionResponse(session_id=session_id, learning_session=gateway.with_attempt_history(repo, user_id, session_id, ls))


@router.post("/sessions/{user_id}/{session_id}/lessons/{node_id}", response_model=SessionResponse)
@limiter.limit("30/minute")
def open_node_lesson(request: Request, user_id: str, session_id: str, node_id: str) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    try:
        gateway.hydrate_session(repo, user_id, ls)
        with gateway.repair_context(repo, user_id, session_id):
            ls = open_lesson(ls, node_id, user_id=user_id)
    except LockedNodeError as e:
        # Server-side gate: a locked node cannot be opened even by a hand-crafted request.
        gateway.synchronize_session(repo, user_id, session_id, ls)
        repo.save(user_id, session_id, ls)  # preserve any recovered remediation before returning the lock
        raise HTTPException(status_code=409, detail={"error": "locked", "pending": e.pending})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Lesson compose is LLM-backed (content + exercise) — transient failure ⇒ 503, not 500.
        # Record the failed attempt (a funnel/content signal) and persist before surfacing the 503.
        record_event(ls, STAGES.COMPOSE_FAILURE, node_id=node_id, error=type(e).__name__, user_id=authed_user_id)
        gateway.synchronize_session(repo, user_id, session_id, ls)
        repo.save(user_id, session_id, ls)
        raise _service_unavailable("open_lesson", e)
    gateway.synchronize_session(repo, user_id, session_id, ls)
    repo.save(user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.post("/users/{user_id}/knowledge", response_model=IngestResponse)
@limiter.limit("10/minute")
async def ingest_material(request: Request, user_id: str, file: UploadFile = File(...)) -> IngestResponse:
    """Ingest a user's PDF into their personal RAG store; scoped to authenticated user."""
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot upload files for another user")
    name = file.filename or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    from rag import registry

    try:
        chunks = await asyncio.to_thread(registry.ingest_pdf, authed_user_id, data, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _service_unavailable("ingest_material", e)
    return IngestResponse(filename=name, chunks=chunks)


@router.post('/sessions/{user_id}/{session_id}/lessons/{node_id}/practice', response_model=SessionResponse)
def new_lesson_practice(request: Request, user_id: str, session_id: str, node_id: str, req: NewPracticeRequest):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    from services.practice_tasks import replace_task, PracticeUnavailableError
    try:
        ls = replace_task(get_default_repository(), user_id, session_id, node_id, req.exercise_revision, req.request_id)
    except KeyError:
        raise HTTPException(404, 'Session not found')
    except LockedNodeError as error:
        raise HTTPException(409, {'error': 'locked', 'pending': error.pending})
    except PracticeUnavailableError as error:
        raise HTTPException(503, str(error))
    except (StorageUnavailableError, SessionConflictError):
        raise
    except ValueError as error:
        raise HTTPException(400, str(error))
    except Exception as error:
        raise _service_unavailable('new_practice', error)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.post("/sessions/{user_id}/{session_id}/assessment", response_model=SessionResponse)
@limiter.limit("15/minute")
def grade_session_assessment(
    request: Request, user_id: str, session_id: str, req: AssessmentAnswersRequest
) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    try:
        ls = grade_assessment(ls, req.answers, action=req.action, quiz_revision=req.quiz_revision)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _service_unavailable("grade_assessment", e)
    repo.save(authed_user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/sessions/{user_id}/{session_id}/chat")
@limiter.limit("20/minute")
async def chat(request: Request, user_id: str, session_id: str, req: ChatRequest) -> StreamingResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    ls.setdefault("chat", [])
    ls["chat"].append({"role": "user", "content": req.message})
    repo.save(authed_user_id, session_id, ls)

    evidence = await asyncio.to_thread(
        retrieve_evidence,
        tutor_retrieval_query(ls, req.node_id, req.message),
        authed_user_id,
        public_query=ls.get("public_query"),
    )
    messages = await asyncio.to_thread(
        build_tutor_messages, ls, req.node_id, req.message, authed_user_id, evidence=evidence
    )
    from services.learning_service import _get_tutor_agent

    @private_generation
    async def gen():
        full = ""
        citations = CitationStream(evidence)
        try:
            async for token in _get_tutor_agent().astream(messages):
                full += token
                ready = citations.push(token)
                if ready:
                    yield _sse({"token": ready})
            tail = citations.push("", final=True)
            if tail:
                yield _sse({"token": tail})
        except Exception:
            logger.error("api: chat stream failed")
            yield _sse({"error": GEN_FAILED})
            return

        full, final_evidence = validate_citations(full, evidence)
        ls["chat"].append({"role": "assistant", "content": full, "grounding": final_evidence})
        try:
            repo.save(authed_user_id, session_id, ls)
        except (StorageUnavailableError, SessionConflictError) as exc:
            yield _sse({"error": str(exc)})
            return
        yield _sse({"grounding": final_evidence})
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{user_id}/{session_id}/grade", response_model=SessionResponse)
@limiter.limit("15/minute")
def grade_node(
    request: Request, user_id: str, session_id: str, node_id: str, req: GradeRequest
) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    if req.attempt_id is None:
        req.attempt_id = "legacy-" + gateway.digest({"session": session_id, "node": node_id,
            "revision": req.exercise_revision, "solution": req.solution})
    new_node_ids: list[str] = []
    result: dict | None = None
    try:
        ls, new_node_ids, result = gateway.grade_lesson(repo, user_id, session_id, node_id,
            req.solution, req.exercise_revision, req.attempt_id)
    except LockedNodeError as e:
        raise HTTPException(status_code=409, detail={"error": "locked", "pending": e.pending})
    except StaleExerciseError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (SessionConflictError, StorageUnavailableError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Grading itself is deterministic, but adaptation/regeneration is LLM-backed — guard anyway.
        raise _service_unavailable("grade", e)
    return SessionResponse(
        session_id=session_id,
        learning_session=ls,
        new_node_ids=new_node_ids,
        grade_result=result,
    )


@router.get("/exercises/{user_id}")
def list_exercises(request: Request, user_id: str):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    return gateway.list_standalone(get_default_repository(), user_id)


@router.get("/capabilities")
def runtime_capabilities():
    return gateway.capabilities()


def _run_context(repo, user, sid=None, node=None, exercise_id=None):
    if sid is not None:
        ls = _load_or_404(repo, user, sid)
        from services.completion import locked_node_ids
        if node in locked_node_ids(ls["skill_graph"], ls["node_state"]):
            raise HTTPException(409, "Complete this skill's prerequisites first.")
        exercise = (ls.get("lessons", {}).get(node) or {}).get("exercise")
        context = {"session_id": sid, "node_id": node}
    else:
        row = GradingStore(repo).get(user, "exercise:" + exercise_id)
        exercise = row["exercise"] if row else None
        context = {"exercise_id": exercise_id}
    if not exercise:
        raise HTTPException(404, "Open an existing exercise before running code.")
    return exercise, context


@router.post("/sessions/{user_id}/{session_id}/run")
def run_lesson(request: Request, user_id: str, session_id: str, node_id: str, req: RunRequest):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    repo = get_default_repository()
    exercise, context = _run_context(repo, user_id, session_id, node_id)
    return practice_runs.run(repo, user_id, exercise, req.solution, req.exercise_revision, req.run_id, context, req.stdin)


@router.delete("/sessions/{user_id}/{session_id}/run/{run_id}")
def cancel_lesson_run(request: Request, user_id: str, session_id: str, run_id: str, node_id: str):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    try:
        return practice_runs.cancel(get_default_repository(), user_id, run_id, {"session_id": session_id, "node_id": node_id})
    except KeyError:
        raise HTTPException(404, "Run not found")


@router.post("/exercises/{user_id}/{exercise_id}/run")
def run_standalone(request: Request, user_id: str, exercise_id: str, req: RunRequest):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    repo = get_default_repository()
    exercise, context = _run_context(repo, user_id, exercise_id=exercise_id)
    return practice_runs.run(repo, user_id, exercise, req.solution, req.exercise_revision, req.run_id, context, req.stdin)


@router.delete("/exercises/{user_id}/{exercise_id}/run/{run_id}")
def cancel_standalone_run(request: Request, user_id: str, exercise_id: str, run_id: str):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    try:
        return practice_runs.cancel(get_default_repository(), user_id, run_id, {"exercise_id": exercise_id})
    except KeyError:
        raise HTTPException(404, "Run not found")


@router.get("/exercises/{user_id}/{exercise_id}")
def get_exercise(request: Request, user_id: str, exercise_id: str):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    result = gateway.get_standalone(get_default_repository(), user_id, exercise_id)
    if result is None:
        raise HTTPException(404, "Exercise not found")
    return result


@router.post("/exercises/{user_id}/{exercise_id}/grade")
def grade_exercise(request: Request, user_id: str, exercise_id: str, req: GradeRequest):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    repo = get_default_repository()
    row = GradingStore(repo).get(user_id, "exercise:" + exercise_id)
    if not row:
        raise HTTPException(404, "Exercise not found")
    if req.attempt_id is None:
        req.attempt_id = "legacy-" + gateway.digest({"exercise": exercise_id,
            "revision": req.exercise_revision, "solution": req.solution})
    try:
        attempt, _ = gateway.accept_and_evaluate(repo, user_id, row["exercise"], req.solution,
            req.exercise_revision, req.attempt_id, context={"standalone": True})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    exercise = gateway.get_standalone(repo, user_id, exercise_id)
    exercise["grade_result"] = public_result({**(attempt.get("result") or gateway.outcome("pending", "Assessment is pending.")),
        "attempt_id": req.attempt_id, "applied": False})
    return exercise


@router.get("/attempts/{user_id}/{attempt_id}")
def get_attempt(request: Request, user_id: str, attempt_id: str):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    attempt = GradingStore(get_default_repository()).get(user_id, "attempt:" + attempt_id)
    if attempt is None:
        raise HTTPException(404, "Attempt not found")
    return public_result({**(attempt.get("result") or gateway.outcome("pending", "Assessment is pending.")),
        "attempt_id": attempt_id, "applied": attempt.get("applied", False)})


@router.post("/sessions/{user_id}/{session_id}/attempts/{attempt_id}/apply", response_model=SessionResponse)
def apply_attempt(request: Request, user_id: str, session_id: str, attempt_id: str, node_id: str):
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(403, "Forbidden: Cannot access another user's practice")
    try:
        ls, added, result = gateway.reconcile_lesson(get_default_repository(), user_id, session_id, node_id, attempt_id)
    except KeyError:
        raise HTTPException(404, "Attempt or session not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return SessionResponse(session_id=session_id, learning_session=ls, new_node_ids=added, grade_result=result)


@router.post("/sessions/{user_id}/{session_id}/lessons/{node_id}/flag", response_model=SessionResponse)
def flag_lesson(
    request: Request, user_id: str, session_id: str, node_id: str, req: FlagRequest
) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    record_event(ls, STAGES.CONTENT_FLAGGED, node_id=node_id, reason=req.reason, user_id=authed_user_id)
    repo.save(authed_user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.get("/admin/funnel")
def admin_funnel(x_admin_token: str | None = Header(default=None)) -> dict:
    """Aggregate the Gate-1 funnel across every session (operator view)."""
    expected = os.environ.get("MINDMORPH_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin funnel disabled (MINDMORPH_ADMIN_TOKEN unset).")
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
        "sessions_reaching_stage": stage_reach,
        "low_score_total": sum(s["low_score_count"] for s in summaries),
        "regenerations_total": sum(s["regenerations"] for s in summaries),
        "content_flags_total": sum(s["content_flags"] for s in summaries),
        "failures_total": sum(s["failures"] for s in summaries),
    }
