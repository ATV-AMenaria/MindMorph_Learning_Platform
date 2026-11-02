"""Explicitly gated real PostgreSQL grading lifecycle checks on a disposable DB."""
import json
import os
import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.getenv("MINDMORPH_REQUIRE_GRADING_DB") != "1", reason="Requires disposable grading verification database")


@pytest.fixture
def saved(monkeypatch):
    from persistence.repository import PostgresLearningSessionRepository
    from services.grading_gateway import synchronize_session
    from services.mastery import default_node_state
    monkeypatch.setattr("services.learning_service.adapt_after_grade", lambda *args: [])
    repo = PostgresLearningSessionRepository()
    user = "grading-db-" + uuid.uuid4().hex
    ls = {"skill_graph": {"nodes": [{"id": "a", "label": "Double"}], "edges": []},
          "node_state": {"a": default_node_state()}, "events": [], "lessons": {"a": {"exercise": {
              "id": "task", "revision": "r1", "format": "coding_challenge", "language": "json",
              "statement": "Reply with a JSON object containing n equal to twice21.",
              "grading_artifact": {"method": "structured", "checks": [{"id": "double", "answer_key": "n", "expected": 42, "max_points": 100}]},
          }}}}
    synchronize_session(repo, user, "s1", ls)
    repo.save(user, "s1", ls)
    yield repo, user
    repo.delete(user, "s1")


def test_result_and_application_survive_fresh_processes_without_regrading(saved):
    from services.grading_gateway import grade_lesson
    repo, user = saved
    ls, _, result = grade_lesson(repo, user, "s1", "a", '{"n":42}', "r1", "once")
    assert result["score"] == 100 and ls["node_state"]["a"]["attempts"] == 1
    code = '''import json,sys
from persistence.repository import PostgresLearningSessionRepository
from services import grading_gateway as g
def fail(*args): raise AssertionError("A saved attempt must not be rejudged")
g._evaluate=fail
ls,_,result=g.grade_lesson(PostgresLearningSessionRepository(),sys.argv[1],"s1","a",'{"n":42}',"r1","once")
print(json.dumps({"attempts":ls["node_state"]["a"]["attempts"],"score":result["score"],"applied":result["applied"]}))
'''
    for _ in range(2):
        response = subprocess.run([sys.executable, "-c", code, user], capture_output=True, text=True, timeout=40)
        assert response.returncode == 0, response.stderr[-1500:]
        assert json.loads(response.stdout.strip().splitlines()[-1]) == {"attempts": 1, "score": 100, "applied": True}


def test_terminal_outcome_survives_conflict_and_applies_once(saved, monkeypatch):
    from services import grading_gateway as g
    from persistence.grading_store import GradingStore
    from persistence.errors import SessionConflictError
    repo, user = saved
    original = g._evaluate
    calls = []
    def concurrent(*args):
        calls.append(1)
        ls = repo.get(user, "s1")
        ls["chat"] = [{"role": "user", "content": "Concurrent note"}]
        repo.save(user, "s1", ls)
        return original(*args)
    monkeypatch.setattr(g, "_evaluate", concurrent)
    with pytest.raises(SessionConflictError):
        g.grade_lesson(repo, user, "s1", "a", '{"n":42}', "r1", "conflict")
    assert GradingStore(repo).get(user, "attempt:conflict")["state"] == "completed"
    assert repo.get(user, "s1")["node_state"]["a"]["attempts"] == 0
    ls, _, result = g.reconcile_lesson(repo, user, "s1", "a", "conflict")
    assert result["applied"] and ls["node_state"]["a"]["attempts"] == 1
    g.reconcile_lesson(repo, user, "s1", "a", "conflict")
    assert repo.get(user, "s1")["node_state"]["a"]["attempts"] == 1 and len(calls) == 1
    assert ls["chat"][0]["content"] == "Concurrent note"


def test_expired_lease_fences_late_worker_and_recovers(saved):
    from persistence.grading_store import GradingStore
    repo, user = saved
    store = GradingStore(repo)
    store.put_once(user, "attempt:lease", "attempt", {"state": "accepted"})
    first, claimed = store.admit_attempt(user, "attempt:lease")
    assert claimed
    store.update(user, "attempt:lease", {"lease_until": 0})
    second, claimed = store.admit_attempt(user, "attempt:lease")
    assert claimed and second["lease_token"] != first["lease_token"]
    stale = store.complete_attempt(user, "attempt:lease", first["lease_token"], {"score": 100})
    assert stale["state"] == "running" and "result" not in stale
    done = store.complete_attempt(user, "attempt:lease", second["lease_token"], {"status": "feedback_only", "score": None})
    assert done["state"] == "completed" and done["result"]["score"] is None


def test_creation_stage_and_lease_survive_a_fresh_process(saved):
    from api.routes import _accept_creation
    from api.schemas import CreateSessionRequest
    from persistence.grading_store import GradingStore
    repo, user = saved
    req = CreateSessionRequest(user_id=user, query='A recovery path', request_id='recovery')
    _accept_creation(req, repo)
    store = GradingStore(repo)
    store.update(user, 'create:recovery', {'lease_until': 0})
    replacement = CreateSessionRequest(user_id=user, query='A recovery path', request_id='recovery')
    _accept_creation(replacement, repo)
    assert replacement._creation_token != req._creation_token
    store.update(user, 'create:recovery', {'state': 'generated', 'result': {'route': 'SCOUT',
        'learning_session': {'graph_identity': 'creation-saved', 'skill_graph': {'nodes': [], 'edges': []},
                             'node_state': {}, 'lessons': {}, 'events': []}}}, expected_token=replacement._creation_token)
    code = '''import sys,json
from api.routes import _accept_creation
from api.schemas import CreateSessionRequest
from persistence.repository import PostgresLearningSessionRepository
req=CreateSessionRequest(user_id=sys.argv[1],query='A recovery path',request_id='recovery')
result=_accept_creation(req,PostgresLearningSessionRepository())
print(json.dumps({'session_id':result.session_id,'route':result.route}))
'''
    for _ in range(2):
        result = subprocess.run([sys.executable, '-c', code, user], capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stderr[-1500:]
        assert json.loads(result.stdout.strip().splitlines()[-1]) == {'session_id': 'creation-saved', 'route': 'SCOUT'}
    assert repo.get(user, 'creation-saved') is not None


def test_memory_admission_includes_controls_across_processes(saved):
    from persistence.grading_store import GradingStore
    repo,user=saved;other=user+'-other';store=GradingStore(repo)
    store.put_once(user,'control:large','run',{'state':'accepted','memory_mb':2048})
    heavy,claimed=store.admit_attempt(user,'control:large');assert claimed
    store.put_once(other,'attempt:small','attempt',{'state':'accepted','memory_mb':512})
    code='''import sys,json
from persistence.repository import PostgresLearningSessionRepository
from persistence.grading_store import GradingStore
record,claimed=GradingStore(PostgresLearningSessionRepository()).admit_attempt(sys.argv[1],'attempt:small')
print(json.dumps({'claimed':claimed,'state':record['state']}))
'''
    result=subprocess.run([sys.executable,'-c',code,other],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr[-1500:]
    assert json.loads(result.stdout.strip().splitlines()[-1])=={'claimed':False,'state':'queued'}
    store.complete_attempt(user,'control:large',heavy['lease_token'],{'status':'succeeded'})
    result=subprocess.run([sys.executable,'-c',code,other],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr[-1500:]
    assert json.loads(result.stdout.strip().splitlines()[-1])=={'claimed':True,'state':'running'}
