"""Publication controls, trusted scoring and durable Run boundaries."""
import pytest

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from persistence.repository import InMemoryLearningSessionRepository
from services import grading_gateway as g
from services import practice_runs


def artifact():
    return {"execution_profile": "python-core", "profile_revision": "1", "entrypoint": "main.py",
        "io_checks": [{"id": "positive", "stdin": "21", "expected_stdout": "42", "max_points": 50},
                      {"id": "zero", "stdin": "0", "expected_stdout": "0", "max_points": 50}],
        "reference_files": {"main.py": "double"}, "alternate_correct_files": [{"main.py": "add"}],
        "negative_control_files": [{"main.py": "constant"}, {"main.py": "identity"}]}


@pytest.fixture
def worker(monkeypatch):
    calls = []
    monkeypatch.setattr(g, "capabilities", lambda: {"python-core": {"available": True, "revision": "1"}})
    def execute(profile, files, **kwargs):
        calls.append((files, kwargs))
        source = files["main.py"]
        n = int(kwargs.get("stdin") or 0)
        if source == "crash":
            return {"status": "learner_error", "exit_code": 1, "stdout": ""}
        value = str(n * 2) if source in {"double", "add"} else "42" if source == "constant" else str(n) if source == "identity" else ""
        return {"status": "succeeded", "exit_code": 0, "stdout": value}
    monkeypatch.setattr("tools.isolated_runner.execute_profile", execute)
    return calls


def test_release_controls_are_persisted_and_reopening_does_not_execute(worker):
    store = GradingStore(InMemoryLearningSessionRepository())
    assert g.verify_evaluator(store, "u", artifact())["verified"]
    count = len(worker)
    assert count >= 10
    assert g.verify_evaluator(store, "u", artifact())["verified"] and len(worker) == count
    assert all(0 < kwargs["deadline_seconds"] <= 120 for _, kwargs in worker)


def test_false_positive_oracle_cannot_publish_even_with_model_claim(worker):
    value = artifact()
    value["preflight_verified"] = True
    value["negative_control_files"][0] = {"main.py": "add"}
    verdict = g.verify_evaluator(GradingStore(InMemoryLearningSessionRepository()), "u", value)
    assert not verdict["verified"]


def test_learner_runtime_error_is_zero_only_when_reference_is_healthy(worker, monkeypatch):
    assert g._execute_checks(artifact(), "crash")["score"] == 0
    original = __import__("tools.isolated_runner", fromlist=["execute_profile"]).execute_profile
    def outage(profile, files, **kwargs):
        if files["main.py"] == "double": return {"status": "unavailable"}
        return original(profile, files, **kwargs)
    monkeypatch.setattr("tools.isolated_runner.execute_profile", outage)
    result = g._execute_checks(artifact(), "crash")
    assert result["status"] == "platform_error" and result["score"] is None


def test_nested_numeric_comparison_rejects_shape_order_boolean_and_nonfinite():
    check = {"expected_stdout": '{"rows":[1.0,2.0]}', "comparison_mode": "json", "absolute_tolerance": .001}
    assert g._compare_output(check, '{"rows":[1.00001,2]}')
    for output in ['{"rows":[2,1]}', '{"rows":[true,2]}', '{"rows":[1,2,3]}', '{"rows":[NaN,2]}']:
        assert not g._compare_output(check, output)


def test_run_replays_without_execution_and_rejects_changed_binding(worker):
    repo = InMemoryLearningSessionRepository()
    exercise = {"id": "task", "revision": "r1", "language": "python"}
    result = practice_runs.run(repo, "u", exercise, "double", "r1", "once", {"session_id": "s"})
    assert result["status"] == "succeeded" and "score" not in result
    count = len(worker)
    assert practice_runs.run(repo, "u", exercise, "double", "r1", "once", {"session_id": "s"}) == result
    assert len(worker) == count
    with pytest.raises(SessionConflictError):
        practice_runs.run(repo, "u", exercise, "identity", "r1", "once", {"session_id": "s"})


def test_run_exception_is_saved_as_nonresult_and_does_not_leave_running(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    def fail(*args, **kwargs): raise OSError("worker unavailable")
    monkeypatch.setattr("tools.isolated_runner.execute_profile", fail)
    result = practice_runs.run(repo, "u", {"id": "task", "revision": "r1", "language": "python"}, "code", "r1", "failed", {})
    assert result["status"] == "platform_error"
    assert GradingStore(repo).get("u", "run:failed")["state"] == "completed"


def test_semantic_repair_is_once_persisted_and_preserves_task(worker, monkeypatch):
    repo = InMemoryLearningSessionRepository()
    broken = artifact()
    broken["negative_control_files"][0] = {"main.py": "add"}
    exercise = {"id": "task", "revision": "r1", "format": "coding_challenge", "language": "python",
        "statement": "Print twice the input", "grading_artifact": broken}
    calls = []
    def repair(saved, old, capability, reason):
        calls.append(saved["statement"])
        return artifact()
    monkeypatch.setattr(g, "_repair_evaluator", repair)
    g.publish_exercise(repo, "u", exercise, context={"standalone": True})
    assert exercise["status"] == "ready" and calls == ["Print twice the input"]
    assert exercise["statement"] == "Print twice the input" and exercise["revision"] == "r1"
    # Recover the original raw generated result after a controller crash.
    original = {**exercise, "grading_artifact": broken}
    g.publish_exercise(repo, "u", original, context={"standalone": True})
    assert original["status"] == "ready" and len(calls) == 1


def test_failed_semantic_repair_and_reopening_do_not_reset_budget(worker, monkeypatch):
    repo = InMemoryLearningSessionRepository()
    value = artifact()
    value["negative_control_files"][0] = {"main.py": "double"}
    exercise = {"id": "task", "revision": "r1", "format": "coding_challenge", "language": "python",
        "statement": "Print twice the input", "grading_artifact": value}
    calls = []
    monkeypatch.setattr(g, "_repair_evaluator", lambda *args: calls.append(1))
    for _ in range(3):
        g.publish_exercise(repo, "u", {**exercise}, context={"standalone": True})
    assert calls == [1]
    assert len(GradingStore(repo).list("u", "repair")) == 1


def test_control_outage_recovers_without_spending_model_repair_budget(worker, monkeypatch):
    import tools.isolated_runner as runner
    store = GradingStore(InMemoryLearningSessionRepository())
    healthy = runner.execute_profile
    monkeypatch.setattr(runner, 'execute_profile', lambda *args, **kwargs: {'status': 'unavailable'})
    result = g.verify_evaluator(store, 'u', artifact())
    assert result['state'] == 'retryable_error' and not result['verified']
    monkeypatch.setattr(runner, 'execute_profile', healthy)
    assert g.verify_evaluator(store, 'u', artifact())['verified']
    assert not store.list('u', 'repair')


def test_new_profile_revision_has_its_own_single_repair_budget(worker, monkeypatch):
    repo = InMemoryLearningSessionRepository()
    monkeypatch.setattr(g, 'capabilities', lambda: {'python-core': {'available': True, 'revision': '2'}})
    calls = []
    def repair(*args):
        calls.append(1)
        return {**artifact(), 'profile_revision': '2'}
    monkeypatch.setattr(g, '_repair_evaluator', repair)
    value = {'id': 'task', 'revision': 'r1', 'format': 'coding_challenge', 'language': 'python',
             'statement': 'Print twice the input', 'grading_artifact': artifact()}
    g.publish_exercise(repo, 'u', value, context={'standalone': True})
    assert value['status'] == 'ready' and calls == [1]


def test_run_passes_stdin_and_binds_it_to_the_durable_identity(worker):
    repo = InMemoryLearningSessionRepository()
    exercise = {'id': 'task', 'revision': 'r1', 'language': 'python'}
    result = practice_runs.run(repo, 'u', exercise, 'double', 'r1', 'input', {}, '21')
    assert result['stdout'] == '42' and worker[-1][1]['stdin'] == '21'
    with pytest.raises(SessionConflictError):
        practice_runs.run(repo, 'u', exercise, 'double', 'r1', 'input', {}, '22')
