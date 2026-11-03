"""JWT ownership must survive the staging/grading route merge."""
import pytest
from fastapi.testclient import TestClient

from api.main import app
from services.security import create_access_token


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/sessions/victim/s/lessons/n/practice", {"request_id": "new"}),
    ("POST", "/sessions/victim/s/run?node_id=n", {"solution": "x", "run_id": "r"}),
    ("DELETE", "/sessions/victim/s/run/r?node_id=n", None),
    ("GET", "/exercises/victim", None),
    ("GET", "/exercises/victim/e", None),
    ("POST", "/exercises/victim/e/run", {"solution": "x", "run_id": "r"}),
    ("DELETE", "/exercises/victim/e/run/r", None),
    ("POST", "/exercises/victim/e/grade", {"solution": "x"}),
    ("GET", "/attempts/victim/a", None),
    ("POST", "/sessions/victim/s/attempts/a/apply?node_id=n", None),
])
def test_new_practice_routes_reject_another_owner(method, path, body):
    client = TestClient(app)
    assert client.request(method, path, json=body).status_code == 401
    token = create_access_token("attacker", "attacker@example.com")
    response = client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/sessions", "/sessions/stream"])
def test_creation_replay_and_storage_use_verified_owner(monkeypatch, path):
    from api import routes
    from persistence.repository import InMemoryLearningSessionRepository
    repo = InMemoryLearningSessionRepository()
    monkeypatch.setattr(routes, "get_default_repository", lambda: repo)
    seen = []

    def generated(*args, **kwargs):
        seen.append(kwargs["user_id"])
        return {"route": "SCOUT", "learning_session": {"skill_graph": {"nodes": [], "edges": []},
            "node_state": {}, "lessons": {}, "events": [], "chat": []}}

    async def streamed(*args, **kwargs):
        yield {"result": generated(*args, **kwargs)}

    monkeypatch.setattr(routes, "start_session", generated)
    monkeypatch.setattr(routes, "astream_session", streamed)
    client = TestClient(app)
    token = create_access_token("owner", "owner@example.com")
    for _ in range(2):
        response = client.post(path, json={"user_id": "victim", "query": "Learn Python", "request_id": "same"},
            headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
    assert seen == ["owner"]
    assert len(repo.list_sessions("owner")) == 1
    assert repo.list_sessions("victim") == []
