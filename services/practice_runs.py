"""Idempotent practice executions. Their observations never award progress."""
import uuid

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from services.grading_gateway import digest


def _public(record, run_id):
    result = record.get("result") or {"status": "pending", "message": "Run accepted. Check its status shortly."}
    return {"run_id": run_id, **{k: v for k, v in result.items() if k in
        {"status", "stdout", "stderr", "exit_code", "duration", "profile_id", "profile_revision", "message"}}}


def run(repo, user, exercise, solution, revision, run_id, context, stdin=""):
    from tools.isolated_runner import execute_profile
    from tools.runtime_profiles import PROFILES
    if revision != exercise.get("revision"):
        raise SessionConflictError("This exercise changed. Reload before running it.")
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
        raise ValueError("A stable run_id is required.")
    store = GradingStore(repo)
    package = store.get(user, "package:" + exercise.get("package_id", "")) or {}
    artifact = package.get("artifact", {})
    profile = artifact.get("execution_profile") or {"python": "python-core", "javascript": "javascript-node",
        "typescript": "typescript-node", "sql": "sqlite"}.get(exercise.get("language"), exercise.get("language"))
    key = "run:" + run_id
    binding = digest({"exercise": exercise.get("id"), "revision": revision, "solution": solution, "stdin": stdin, "context": context})
    record, _ = store.put_once(user, key, "run", {"state": "accepted", "binding": binding,
        "context": context, "worker_run_id": uuid.uuid4().hex, 'memory_mb':PROFILES[profile].memory_mb if profile in PROFILES else 0})
    if record["binding"] != binding:
        raise SessionConflictError("This run ID belongs to different code or an exercise revision.")
    if record.get("state") in {"completed", "cancelled"}:
        return _public(record, run_id)
    record, claimed = store.admit_attempt(user, key)
    if not claimed:
        return _public(record, run_id)
    if profile not in PROFILES:
        result = {"status": "unavailable", "message": "No tested runtime is available for this language. Your draft is preserved."}
    else:
        entry = artifact.get("entrypoint") or PROFILES[profile].entrypoint
        files = {**artifact.get("fixtures", {}), entry: solution}
        try:
            result = execute_profile(profile, files, entrypoint=entry, stdin=stdin, run_id=record["worker_run_id"])
        except Exception:
            result = {"status": "platform_error", "message": "The runtime could not finish this run. Your draft is preserved."}
    record = store.complete_attempt(user, key, record["lease_token"], result)
    return _public(record, run_id)


def cancel(repo, user, run_id, context):
    from tools.isolated_runner import cancel_run
    store = GradingStore(repo)
    key = "run:" + run_id
    record = store.get(user, key)
    if not record or record.get("context") != context:
        raise KeyError("Run not found")
    if record.get("state") == "completed":
        return _public(record, run_id)
    record = store.update(user, key, {"state": "cancelled", "result": {"status": "cancelled"}})
    cancel_run(record["worker_run_id"])
    return _public(record, run_id)
