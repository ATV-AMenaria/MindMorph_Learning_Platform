"""BKT evidence and summaries commit with progress on a disposable PostgreSQL DB."""
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from persistence.repository import PostgresLearningSessionRepository
from services import grading_gateway as gateway
from services.mastery import apply_score
from tests.test_knowledge_tracking import install, submit

pytestmark = pytest.mark.skipif(os.getenv("MINDMORPH_REQUIRE_GRADING_DB") != "1",
                                reason="Requires disposable grading verification database")


@pytest.fixture
def saved(monkeypatch):
    monkeypatch.setenv("MINDMORPH_BKT", "1")
    monkeypatch.setattr("services.learning_service.adapt_after_grade", lambda *args: [])
    repo, user = PostgresLearningSessionRepository(), "bkt-db-" + uuid.uuid4().hex
    yield repo, user
    repo.delete(user, "s")


def test_bkt_history_and_public_summary_survive_fresh_process_without_recalculation(saved):
    repo, user = saved
    for index, success in enumerate([False, False, True]):
        ex = install(repo, user=user, identity=f"e-{index}")
        ls, _, _ = submit(repo, ex, user=user, attempt=f"a-{index}", success=success)
    expected = ls["node_state"]["a"]["knowledge_tracking"]
    assert expected["recommendation"]["mode"] == "foundation"
    code = '''import sys,json
from persistence.repository import PostgresLearningSessionRepository
from persistence.grading_store import GradingStore
from services import grading_gateway as g, knowledge_tracking as k
from services.public_projection import public_session
def fail(*args): raise AssertionError("Saved evidence must not be rejudged or recomputed on replay")
g._evaluate=fail
k.predict_history=fail
repo=PostgresLearningSessionRepository()
ls,_,_=g.grade_lesson(repo,sys.argv[1],"s","a",json.dumps({"a":42,"b":6}),"e-2-r1","a-2")
print(json.dumps({"summary":public_session(ls)["node_state"]["a"]["knowledge_tracking"],
                  "count":len(GradingStore(repo).knowledge_observations(sys.argv[1],"s","a"))}))
'''
    result = subprocess.run([sys.executable, "-c", code, user], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr[-1500:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"summary": expected, "count": 3}
    assert GradingStore(repo).knowledge_observations(user + "-foreign", "s", "a") == []


def test_conflict_rolls_back_ledger_and_summary_then_reconciles_once(saved, monkeypatch):
    repo, user = saved
    ex = install(repo, user=user)
    evaluate = gateway._evaluate
    def concurrent(*args):
        current = repo.get(user, "s")
        current["chat"] = [{"role": "user", "content": "Concurrent work"}]
        repo.save(user, "s", current)
        return evaluate(*args)
    monkeypatch.setattr(gateway, "_evaluate", concurrent)
    with pytest.raises(SessionConflictError):
        submit(repo, ex, user=user)
    store = GradingStore(repo)
    assert store.knowledge_observations(user, "s", "a") == []
    assert "knowledge_tracking" not in repo.get(user, "s")["node_state"]["a"]
    for _ in range(2):
        gateway.reconcile_lesson(repo, user, "s", "a", "once")
    assert len(store.knowledge_observations(user, "s", "a")) == 1
    assert repo.get(user, "s")["node_state"]["a"]["knowledge_tracking"]["observation_count"] == 1


def test_concurrent_submissions_cannot_insert_duplicate_evidence(saved):
    repo, user = saved
    install(repo, user=user)
    store = GradingStore(repo)
    result = {"status": "graded", "method": "execution", "passed": 1, "total": 1, "failures": []}
    snapshots = [repo.get(user, "s"), repo.get(user, "s")]
    for index, ls in enumerate(snapshots):
        store.put_once(user, f"attempt:concurrent-{index}", "attempt", {"state": "completed", "applied": False,
            "attempt_id": f"concurrent-{index}", "exercise_id": "task", "revision": "task-r1",
            "context": {"session_id": "s", "node_id": "a"}, "result": result})
        apply_score(ls, "a", "coding_challenge", result)
    barrier = Barrier(2)
    def apply(index):
        barrier.wait(timeout=10)
        try:
            store.apply_session(user, "s", snapshots[index], f"attempt:concurrent-{index}", [])
            return "applied"
        except SessionConflictError:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(apply, [0, 1]))
    assert sorted(results) == ["applied", "conflict"]
    assert len(store.knowledge_observations(user, "s", "a")) == 1
    index = results.index("conflict")
    ls = repo.get(user, "s")
    apply_score(ls, "a", "coding_challenge", result)
    store.apply_session(user, "s", ls, f"attempt:concurrent-{index}", [])
    assert len(store.knowledge_observations(user, "s", "a")) == 1
    assert repo.get(user, "s")["node_state"]["a"]["knowledge_tracking"]["observation_count"] == 1
