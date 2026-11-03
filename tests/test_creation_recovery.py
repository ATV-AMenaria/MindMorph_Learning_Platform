"""Crash windows and stale producers in learning creation, without model calls."""
import pytest
from fastapi import HTTPException

from api import routes
from api.schemas import CreateSessionRequest
from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from persistence.repository import InMemoryLearningSessionRepository


def request():
    return CreateSessionRequest(user_id="owner", query="Learn Python", request_id="stable")


def generated():
    return {"route": "SCOUT", "learning_session": {"skill_graph": {"nodes": [], "edges": []},
        "node_state": {}, "lessons": {}, "events": [], "chat": []}}


def test_only_one_creation_producer_and_expired_lease_recovers():
    repo = InMemoryLearningSessionRepository()
    first, second = request(), request()
    assert routes._accept_creation(first, repo) is None
    with pytest.raises(HTTPException) as error:
        routes._accept_creation(second, repo)
    assert error.value.status_code == 409
    GradingStore(repo).update("owner", "create:stable", {"lease_until": 0})
    assert routes._accept_creation(second, repo) is None
    assert first._creation_token != second._creation_token
    with pytest.raises(SessionConflictError):
        routes._finish_creation(first, repo, generated())
    routes._creation_failed(first, repo)
    assert GradingStore(repo).get("owner", "create:stable")["state"] == "accepted"
    result = routes._finish_creation(second, repo, generated())
    assert routes._accept_creation(request(), repo) == result
    assert len(repo.list_sessions("owner")) == 1


def test_generation_survives_crash_before_first_session_save(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    req = request()
    routes._accept_creation(req, repo)
    save = repo.save
    monkeypatch.setattr(repo, "save", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        routes._finish_creation(req, repo, generated())
    routes._creation_failed(req, repo)
    staged = GradingStore(repo).get("owner", "create:stable")
    assert staged["state"] == "generated"
    identity = staged["result"]["learning_session"]["graph_identity"]
    monkeypatch.setattr(repo, "save", save)
    recovered = routes._accept_creation(request(), repo)
    assert recovered.session_id == identity and len(repo.list_sessions("owner")) == 1


def test_crash_after_save_reuses_identity_and_preserves_new_learner_progress(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    req = request()
    routes._accept_creation(req, repo)
    update = GradingStore.update
    def fail_completion(self, user, key, changes, **kwargs):
        if changes.get("state") == "completed": raise OSError("interrupted")
        return update(self, user, key, changes, **kwargs)
    monkeypatch.setattr(GradingStore, "update", fail_completion)
    with pytest.raises(OSError): routes._finish_creation(req, repo, generated())
    identity = repo.list_sessions("owner")[0]["session_id"]
    session = repo.get("owner", identity)
    session["chat"] = [{"role": "user", "content": "Keep this newer work"}]
    repo.save("owner", identity, session)
    monkeypatch.setattr(GradingStore, "update", update)
    result = routes._accept_creation(request(), repo)
    assert result.session_id == identity and result.learning_session["chat"][0]["content"] == "Keep this newer work"
    assert len(repo.list_sessions("owner")) == 1


def test_recovery_never_changes_original_preferences():
    repo = InMemoryLearningSessionRepository()
    routes._accept_creation(request(), repo)
    changed = request()
    changed.query = "Different goal"
    with pytest.raises(HTTPException): routes._accept_creation(changed, repo)
    assert not repo.list_sessions("owner")
