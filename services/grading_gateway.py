"""Shared publication, submission and replay lifecycle for lesson/standalone tasks.

Worker observations are compared here against private server-owned I/O checks.
Ordinary requests never execute learner code on the API host.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
import uuid
import time

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore, clone
from services.grading_results import normalize_grade_result, eligible_score
from services.public_projection import public_exercise, public_result
from tools.grading_contract import language_identity
from services.generation_privacy import private_generation

_repair_context = ContextVar("grading_repair_context", default=None)
_evaluation_deadline = ContextVar("grading_evaluation_deadline", default=None)
_control_admission = ContextVar('grading_control_admission', default=None)


def _worker_budget():
    deadline = _evaluation_deadline.get()
    if deadline is None:
        return 120
    remaining = deadline - time.monotonic()
    if remaining < 1:
        raise TimeoutError("Assessment work budget exhausted")
    return min(120, remaining)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@contextmanager
def repair_context(repo, user, sid):
    token = _repair_context.set((GradingStore(repo), user, sid))
    try:
        yield
    finally:
        _repair_context.reset(token)


def reserve_repair(node, fingerprint):
    context = _repair_context.get()
    if context is None:
        return True
    store, user, sid = context
    _, accepted = store.put_once(user, f"repair:{sid}:{node}:{fingerprint}", "repair",
        {"fingerprint": fingerprint, "state": "reserved", "attempts": 1})
    return accepted


def outcome(status, feedback, method="execution", **extra):
    return {"status": status, "method": method, "score": None,
        "mastery_eligible": False, "eligible": False, "feedback": feedback, **extra}


def capabilities():
    try:
        from tools.isolated_runner import get_capabilities
        return get_capabilities()
    except (ImportError, OSError):
        return {}


def readiness(exercise, artifact, verification=None):
    if exercise.get("format") == "case_study" or artifact.get("method") == "rubric":
        return "feedback_only" if artifact.get("rubric") else "needs_evidence"
    if artifact.get("method") == "structured":
        try:
            _structured_spec(artifact)
            return "ready"
        except (ValueError, TypeError, KeyError):
            return "needs_evidence"
    profile = artifact.get("execution_profile")
    capability = capabilities().get(profile, {})
    if capability.get("available") and artifact.get("io_checks") and (verification or {}).get("verified") is True:
        return "ready"
    return "needs_evidence"


@private_generation
def _repair_evaluator(exercise, artifact, capability, reason):
    """One semantic repair; the caller has already reserved its durable budget."""
    from agents.exercise.grader_agent import GraderAgent
    from tools.runtime_profiles import PROFILES, execution_contract
    profile = artifact.get("execution_profile")
    contract = {"profile_id": profile, "revision": capability["revision"],
        "entrypoint": PROFILES[profile].entrypoint, "packages": capability.get("packages", []),
        "contract": execution_contract(profile)}
    revised = GraderAgent().build_grading_artifact("Repair this saved exercise's evaluator.",
        exercise["format"], exercise["statement"], exercise_language=exercise.get("language"),
        execution_capability=contract, repair_feedback=reason,
        previous_artifact={k:v for k,v in artifact.items() if k not in {'exercise_statement','instructions'}}, max_attempts=1)
    if revised is None:
        return None
    candidate = revised.model_dump()
    if candidate.get("method") != "execution":
        return None
    candidate["exercise_statement"] = exercise["statement"]
    candidate.pop("preflight_verified", None)
    return candidate


def _release_with_repair(store, user, exercise, artifact, origin):
    verification = verify_evaluator(store, user, artifact) if artifact.get("io_checks") else {}
    capability = capabilities().get(artifact.get("execution_profile"), {})
    if (verification.get("verified") or not artifact.get("io_checks") or origin != "generated"
            or not capability.get("available") or verification.get("state") in {"accepted", "reserved", "retryable_error"}):
        return artifact, verification
    key = "semantic-repair:" + digest({"exercise": exercise["id"], "revision": exercise["revision"],
        "artifact": artifact, "capability_revision": capability["revision"]})
    row, reserved = store.put_once(user, key, "repair", {"state": "reserved", "attempts": 1})
    if not reserved:
        # A controller crash cannot reset the model budget. A completed repair
        # can be reused even if the original request died before saving its lesson.
        return (row["artifact"], row["verification"]) if row.get("artifact") else (artifact, verification)
    try:
        candidate = _repair_evaluator(exercise, artifact, capability, verification.get("reason", "Release controls failed"))
        if candidate:
            checked = verify_evaluator(store, user, candidate)
            store.update(user, key, {"state": "completed", "artifact": candidate, "verification": checked})
            return candidate, checked
    except Exception:
        pass
    store.update(user, key, {"state": "completed", "reason": "The one automatic evaluator repair did not produce a verified evaluator."})
    return artifact, verification


def publish_exercise(repo, user, exercise, *, context, origin="generated"):
    """Persist private data first, then replace inline artifact with a package pointer."""
    store = GradingStore(repo)
    exercise.setdefault("id", uuid.uuid4().hex)
    exercise.setdefault("revision", uuid.uuid4().hex)
    exercise["language"] = language_identity(exercise.get("language"), "python" if exercise.get("format") == "coding_challenge" else None)
    if "grading_artifact" not in exercise and exercise.get("package_id"):
        package = store.get(user, "package:" + exercise["package_id"])
        if package is None:
            exercise["status"] = "needs_evidence"
            return exercise
        verification = store.get(user, package.get("verification_key", "")) or package.get("verification")
        exercise["status"] = readiness(exercise, package["artifact"], verification)
        return exercise
    artifact = clone(exercise.get("grading_artifact") or {})
    if artifact and not isinstance(artifact, dict):
        artifact = {}
    artifact["exercise_statement"] = exercise.get("statement", "")
    artifact.pop("preflight_verified", None)  # model output is never release evidence
    if artifact.get("rubric"):
        for index, criterion in enumerate(artifact["rubric"]):
            criterion.setdefault("criterion_id", f"c{index + 1}")
    artifact, verification = _release_with_repair(store, user, exercise, artifact, exercise.get("origin", origin))
    private = {"exercise_id": exercise["id"], "revision": exercise["revision"], "context": context,
        "origin": exercise.get("origin", origin), "artifact": artifact,
        "task_context": {"statement": exercise.get("statement"), "grounding": exercise.get("grounding"),
            "language": exercise.get("language"), "format": exercise.get("format")}}
    package_id = digest(private)
    exercise["package_id"] = package_id
    exercise["evaluator_revision"] = digest(artifact)[:24]
    exercise["status"] = readiness(exercise, artifact, verification)
    exercise["availability"] = {"status": exercise["status"], "reason":
        "Independent rubric calibration is pending." if exercise["status"] == "feedback_only" else
        "A verified execution profile and evaluator are required." if exercise["status"] == "needs_evidence" else "Assessment is available."}
    if artifact.get("instructions"):
        exercise["instructions"] = artifact["instructions"]
    if artifact.get("rubric"):
        exercise["rubric"] = [{"criterion_id": c["criterion_id"], "criterion": c.get("criterion"),
            "max_points": c.get("weight")} for c in artifact["rubric"]]
    if artifact.get("execution_profile"):
        exercise["execution_profile"] = artifact["execution_profile"]
    private.update(package_id=package_id, public=public_exercise(exercise), package_hash=package_id,
        verification=verification, verification_key=verification.get("record_key"))
    store.put_once(user, "package:" + package_id, "package", private)
    exercise.pop("grading_artifact", None)
    return exercise


def synchronize_session(repo, user, sid, ls):
    for node, lesson in ls.get("lessons", {}).items():
        exercise = lesson.get("exercise")
        if isinstance(exercise, dict) and exercise.get("statement"):
            publish_exercise(repo, user, exercise, context={"session_id": sid, "node_id": node}, origin="legacy")
    return ls


def hydrate_session(repo, user, ls):
    """Private transient view used only while servicing a lesson/evaluator repair."""
    store = GradingStore(repo)
    for lesson in ls.get("lessons", {}).values():
        exercise = lesson.get("exercise") or {}
        if exercise.get("package_id") and "grading_artifact" not in exercise:
            package = store.get(user, "package:" + exercise["package_id"])
            if package:
                artifact = clone(package["artifact"])
                artifact.pop("exercise_statement", None)
                exercise["grading_artifact"] = artifact if any(artifact.values()) else None
    return ls


def save_standalone(repo, user, exercise):
    publish_exercise(repo, user, exercise, context={"standalone": True})
    store = GradingStore(repo)
    store.put_once(user, "exercise:" + exercise["id"], "exercise", {"exercise": clone(exercise),
        "created_at": datetime.now(timezone.utc).isoformat()})
    return get_standalone(repo, user, exercise["id"])


def get_standalone(repo, user, exercise_id):
    store = GradingStore(repo)
    row = store.get(user, "exercise:" + exercise_id)
    if not row:
        return None
    exercise = clone(row["exercise"])
    attempts = [v for v in store.list(user, "attempt") if v.get("exercise_id") == exercise_id]
    attempts.sort(key=lambda v: (v["created_at"], v["attempt_id"]))
    results = [public_result({**v["result"], "attempt_id": v["attempt_id"], "applied": False})
        for v in attempts if v.get("result")]
    exercise.update(created_at=row["created_at"], attempts=len(attempts), history=results,
        last_result=results[-1] if results else None)
    return public_exercise(exercise)


def list_standalone(repo, user):
    return [get_standalone(repo, user, row["exercise"]["id"]) for row in GradingStore(repo).list(user, "exercise")]


def with_attempt_history(repo, user, sid, ls):
    """Read-only public history; non-results never enter node mastery state."""
    from copy import deepcopy
    out = deepcopy(ls)
    attempts = [v for v in GradingStore(repo).list(user, "attempt")
                if v.get("context", {}).get("session_id") == sid and v.get("result")]
    attempts.sort(key=lambda v: (v.get("created_at", ""), v["attempt_id"]))
    for node, lesson in out.get("lessons", {}).items():
        exercise = lesson.get("exercise") or {}
        matching = [public_result({**v["result"], "attempt_id": v["attempt_id"], "applied": v.get("applied", False)})
                    for v in attempts if v["context"].get("node_id") == node and v.get("revision") == exercise.get("revision")]
        if matching:
            exercise["last_result"] = matching[-1]
            exercise["history"] = matching[-20:]
    return out


@private_generation
def _evaluate(package, solution):
    artifact = package["artifact"]
    method = artifact.get("method", "execution" if package["task_context"]["format"] == "coding_challenge" else "rubric")
    if not solution.strip():
        return outcome("invalid_submission", "Enter a solution before submitting.", method)
    if len(solution.encode()) > 256 * 1024:
        return outcome("invalid_submission", "The submission exceeds the file size limit.", method)
    if method == "rubric":
        from agents.exercise.grader_agent import _grade_case_study
        return normalize_grade_result(_grade_case_study(solution, artifact), method="rubric", calibrated=False)
    if method == "structured":
        return _structured(artifact, solution)
    if artifact.get("io_checks") and package.get("verification", {}).get("verified") is True:
        return _execute_checks(artifact, solution)
    if package.get("origin") == "legacy" and os.getenv("MINDMORPH_TRUSTED_LOCAL_GRADING") == "1":
        from agents.exercise.grader_agent import grade_submission
        return normalize_grade_result(grade_submission(package["task_context"]["format"], solution, artifact,
            expected_language=package["task_context"].get("language")), method="execution")
    return outcome("needs_evidence", "This exercise needs a verified isolated evaluator before it can award progress.", method)


def _io_spec(artifact):
    import math
    checks = artifact.get("io_checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 100:
        raise ValueError("A bounded nonempty check suite is required")
    if len({c.get("id") for c in checks}) != len(checks) or any(not isinstance(c.get("id"), str) or not c["id"] for c in checks):
        raise ValueError("Check IDs must be unique")
    for check in checks:
        if not isinstance(check.get("stdin", ""), str) or not isinstance(check.get("expected_stdout"), str):
            raise ValueError("Checks require text input/output")
        weight = check.get("max_points")
        if type(weight) not in (int, float) or not math.isfinite(weight) or not math.isclose(weight, 100 / len(checks), abs_tol=1e-8):
            raise ValueError("Execution checks require equal positive weights totalling 100")
        if len(check.get("stdin", "").encode()) > 65536 or len(check["expected_stdout"].encode()) > 65536:
            raise ValueError("Check input/output exceeds the limit")
        _comparison_policy(check)
    if not isinstance(artifact.get("entrypoint"), str) or not artifact["entrypoint"]:
        raise ValueError("A profile entrypoint is required")
    return checks


def _comparison_policy(check):
    import math
    mode = check.get("comparison_mode", "text")
    absolute, relative = check.get("absolute_tolerance", 0), check.get("relative_tolerance", 0)
    if mode not in {"text", "json", "numeric"} or check.get("unordered", False):
        raise ValueError("Unsupported comparison policy")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (absolute, relative)) or not 0 <= absolute <= 1e6 or not 0 <= relative <= 0.01:
        raise ValueError("Comparison tolerances must be finite and bounded")
    if mode == "text" and (absolute or relative):
        raise ValueError("Text comparison does not accept numeric tolerance")
    if mode in {"json", "numeric"}:
        expected = _finite_json(check["expected_stdout"])
        if mode == "numeric" and (type(expected) not in (int, float) or not math.isfinite(expected)):
            raise ValueError("Numeric output requires a finite JSON number")
    return mode, absolute, relative


def _finite_json(text):
    import math
    def invalid(value):
        raise ValueError("Non-finite JSON values are not supported")
    value = json.loads(text, parse_constant=invalid)
    def validate(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON values are not supported")
        if isinstance(item, dict):
            for child in item.values(): validate(child)
        elif isinstance(item, list):
            for child in item: validate(child)
    validate(value)
    return value


def _compare_output(check, actual):
    import math
    mode, absolute, relative = _comparison_policy(check)
    if not isinstance(actual, str) or len(actual.encode()) > 65536:
        return False
    if mode == "text":
        return actual.replace("\r\n", "\n").rstrip("\n") == check["expected_stdout"].replace("\r\n", "\n").rstrip("\n")
    try:
        observed, expected = _finite_json(actual), _finite_json(check["expected_stdout"])
    except (ValueError, TypeError):
        return False
    def same(a, b):
        if type(a) in (int, float) and type(b) in (int, float):
            return math.isclose(a, b, abs_tol=absolute, rel_tol=relative)
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
        return a == b
    return same(observed, expected)


class ControlEnvironmentUnavailable(RuntimeError):
    pass


def _observed_checks(artifact, files):
    from tools.isolated_runner import execute_profile
    checks = _io_spec(artifact)
    results = []
    for check in checks:
        admission = _control_admission.get()
        kwargs = {'entrypoint':artifact['entrypoint'],'stdin':check.get('stdin',''),'run_id':uuid.uuid4().hex,'deadline_seconds':_worker_budget()}
        if admission:
            from tools.runtime_profiles import PROFILES
            store, user, verification, proof_token = admission
            key = 'control:' + digest([verification,proof_token,files,check['id']])
            store.put_once(user,key,'run',{'state':'accepted','memory_mb':PROFILES[artifact['execution_profile']].memory_mb})
            queued_until = time.monotonic() + min(30,_worker_budget())
            while True:
                job, claimed = store.admit_attempt(user,key)
                if claimed or job.get('state')=='completed': break
                if time.monotonic() >= queued_until: raise ControlEnvironmentUnavailable('Control queue deadline')
                time.sleep(.1)
            if job.get('state')=='completed': observed=job.get('result') or {'status':'unavailable'}
            else:
                kwargs['deadline_seconds']=min(110,_worker_budget())
                try:
                    observed=execute_profile(artifact['execution_profile'],{**artifact.get('fixtures',{}),**files},**kwargs)
                except Exception:
                    observed={'status':'platform_error','stdout':'','stderr':'Control worker failed'}
                job=store.complete_attempt(user,key,job['lease_token'],observed)
                if job.get('state')!='completed': raise ControlEnvironmentUnavailable('Control lease expired')
        else:
            observed=execute_profile(artifact['execution_profile'],{**artifact.get('fixtures',{}),**files},**kwargs)
        if observed.get("status") in {"learner_error", "compile_error", "compilation_error", "time_limit", "timed_out"}:
            results.append(False)
            continue
        if observed.get("status") not in {"completed", "succeeded", "success"}:
            raise ControlEnvironmentUnavailable("Worker did not complete")
        results.append(observed.get("exit_code") == 0 and _compare_output(check, observed.get("stdout", "")))
    return results


def verify_evaluator(store, user, artifact):
    """One persisted release check per exact evaluator/profile/control fingerprint."""
    profile = artifact.get("execution_profile")
    capability = capabilities().get(profile, {})
    if not capability.get("available") or capability.get("revision") != artifact.get("profile_revision"):
        return {"verified": False, "reason": "Matching execution profile is unavailable."}
    key = "verification:" + digest({"artifact": artifact, "capability_revision": capability.get("revision")})
    row, _ = store.put_once(user, key, "verification", {"verified": False, "state": "accepted", "record_key": key})
    if row.get("state") == "completed":
        return row
    row, claimed = store.claim_generation(user, key, seconds=180, states={"accepted", "reserved", "retryable_error"})
    if not claimed:
        return row
    token = row['lease_token']
    deadline_token = _evaluation_deadline.set(time.monotonic() + 170)
    admission_token = _control_admission.set((store,user,key,token))
    try:
        _io_spec(artifact)
        reference = artifact.get("reference_files")
        alternatives = artifact.get("alternate_correct_files")
        negatives = artifact.get("negative_control_files")
        if not reference or not alternatives or not isinstance(alternatives, list) or not isinstance(negatives, list) or len(negatives) < 2:
            raise ValueError("Independent positive and negative controls are required")
        if any(digest(files) == digest(reference) for files in alternatives):
            raise ValueError("Alternate correct controls must differ from the reference")
        observed = _observed_checks(artifact, reference)
        if not all(observed):
            raise ValueError("Reference failed check indexes " + str([i for i, passed in enumerate(observed) if not passed]))
        for index, files in enumerate(alternatives):
            observed = _observed_checks(artifact, files)
            if not all(observed):
                raise ValueError("Alternate correct control " + str(index) + " failed check indexes " + str([i for i, passed in enumerate(observed) if not passed]))
        # Parent-owned empty/no-op control is mandatory even when the model's
        # supplied negatives happen to agree with its proposed oracle.
        controls = [*negatives, {artifact["entrypoint"]: ""}]
        for index, files in enumerate(controls):
            if all(_observed_checks(artifact, files)):
                raise ValueError("The evaluator accepted negative/no-op control index " + str(index))
        return store.update(user, key, {"verified": True, "state": "completed", "profile_revision": capability["revision"]}, expected_token=token)
    except (ControlEnvironmentUnavailable, TimeoutError):
        return store.update(user, key, {"verified": False, "state": "retryable_error",
            "reason": "The control environment did not finish. Retry availability without changing the evaluator."}, expected_token=token)
    except Exception as error:
        reason = str(error)[:240] if isinstance(error, ValueError) else type(error).__name__
        return store.update(user, key, {"verified": False, "state": "completed", "reason": "Evaluator controls did not establish dependable assessment: " + reason}, expected_token=token)
    finally:
        _control_admission.reset(admission_token)
        _evaluation_deadline.reset(deadline_token)


def _structured(artifact, solution):
    try:
        answer = json.loads(solution)
        checks = _structured_spec(artifact)
        outcomes = []
        for check in checks:
            actual = answer[check["answer_key"]]
            expected = check["expected"]
            comparator = check.get("comparator", "exact")
            if comparator == "exact":
                passed = type(actual) is type(expected) and actual == expected
            elif comparator == "numeric":
                import math
                tolerance = check.get("absolute_tolerance", 0)
                if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError("Invalid numeric tolerance")
                passed = type(actual) in (int, float) and type(expected) in (int, float) and math.isfinite(actual) and abs(actual - expected) <= tolerance
            else:
                raise ValueError("Unknown comparator")
            outcomes.append({"criterion_id": check["id"], "max_points": check["max_points"],
                "earned_points": check["max_points"] if passed else 0,
                "evidence": ["Submitted field satisfies the criterion." if passed else "Submitted field does not satisfy the criterion."]})
        return normalize_grade_result({"status": "graded", "criteria": outcomes}, method="structured")
    except (json.JSONDecodeError, KeyError, TypeError):
        return outcome("invalid_submission", "Submit a JSON object with the required answer fields.", "structured")
    except ValueError:
        return outcome("platform_error", "The structured evaluator is invalid.", "structured")


def _structured_spec(artifact):
    import math
    checks = artifact.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 100:
        raise ValueError("Structured assessment requires nonempty bounded checks")
    ids, keys, total = set(), set(), 0.0
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Invalid check")
        identifier, key = check.get("id"), check.get("answer_key")
        if not isinstance(identifier, str) or not identifier or identifier in ids or not isinstance(key, str) or not key or key in keys:
            raise ValueError("Check and answer field identifiers must be unique")
        ids.add(identifier); keys.add(key)
        weight = check.get("max_points")
        if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 < weight <= 100:
            raise ValueError("Invalid maximum points")
        total += weight
        json.dumps(check["expected"], allow_nan=False)
        comparator = check.get("comparator", "exact")
        if comparator not in {"exact", "numeric"}:
            raise ValueError("Unknown comparator")
        if comparator == "numeric":
            expected, tolerance = check["expected"], check.get("absolute_tolerance", 0)
            if type(expected) not in (int, float) or not math.isfinite(expected) or type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1e6:
                raise ValueError("Numeric comparators require finite expected value and bounded tolerance")
    if not math.isclose(total, 100, abs_tol=1e-8):
        raise ValueError("Structured check maxima must sum to 100")
    return checks


def _execute_checks(artifact, solution):
    """Trusted parent computes totals; worker output cannot set points/status counts."""
    profile = artifact.get("execution_profile")
    capability = capabilities().get(profile, {})
    if not capability.get("available") or capability.get("revision") != artifact.get("profile_revision"):
        return outcome("needs_evidence", "The matching execution profile is unavailable or changed.")
    try:
        checks = _io_spec(artifact)
        from tools.isolated_runner import execute_profile
        observations = []
        for check in checks:
            observation = execute_profile(profile, {**artifact.get("fixtures", {}), artifact["entrypoint"]: solution},
                entrypoint=artifact["entrypoint"], stdin=check.get("stdin", ""), run_id=uuid.uuid4().hex, deadline_seconds=_worker_budget())
            learner_failure = observation.get("status") in {"learner_error", "compile_error", "compilation_error", "time_limit", "timed_out"}
            if learner_failure:
                control = execute_profile(profile, {**artifact.get("fixtures", {}), **artifact["reference_files"]},
                    entrypoint=artifact["entrypoint"], stdin=check.get("stdin", ""), run_id=uuid.uuid4().hex, deadline_seconds=_worker_budget())
                if control.get("status") != "succeeded" or control.get("exit_code") != 0 or not _compare_output(check, control.get("stdout", "")):
                    return outcome("platform_error", "The reference environment could not be verified; your progress is unchanged.")
            if not learner_failure and observation.get("status") not in {"completed", "succeeded", "success"}:
                return outcome("retryable_error" if observation.get("status") in {"unavailable", "busy", "queued", "cancelled"} else "platform_error",
                    "Execution did not complete. Your solution is preserved.")
            passed = not learner_failure and observation.get("exit_code") == 0 and _compare_output(check, observation.get("stdout", ""))
            observations.append({"criterion_id": check["id"], "max_points": check["max_points"],
                "earned_points": check["max_points"] if passed else 0, "evidence": ["Output matched." if passed else "Output did not match."]})
        # Structured points support unequal trusted check weights; method execution
        # additionally retains server-owned completed check counts for legacy consumers.
        total = len(observations)
        passed = sum(c["earned_points"] == c["max_points"] for c in observations)
        # Execution scores use equal checks; publication rejects mismatched weights.
        return normalize_grade_result({"status": "graded", "complete": True, "passed": passed, "total": total,
            "failures": ["A private check did not pass."] if passed < total else []}, method="execution")
    except Exception:
        return outcome("platform_error", "The isolated evaluator could not produce validated evidence.")


def accept_and_evaluate(repo, user, exercise, solution, revision, attempt_id, *, context):
    if not attempt_id or not isinstance(attempt_id, str) or len(attempt_id) > 128:
        raise ValueError("A stable attempt_id is required for submissions.")
    if revision != exercise.get("revision"):
        raise SessionConflictError("This exercise has changed. Reload before submitting.")
    store = GradingStore(repo)
    package = store.get(user, "package:" + exercise.get("package_id", ""))
    if package is None:
        raise ValueError("No saved evaluator package exists for this exercise.")
    binding = digest({"exercise_id": exercise["id"], "revision": revision,
        "package_hash": package["package_hash"], "submission_hash": digest(solution), "context": context})
    key = "attempt:" + attempt_id
    from tools.runtime_profiles import PROFILES
    profile = PROFILES.get(package.get('artifact',{}).get('execution_profile'))
    memory_mb = profile.memory_mb if profile else 0
    attempt, created = store.put_once(user, key, "attempt", {"attempt_id": attempt_id, "binding": binding,
        "exercise_id": exercise["id"], "revision": revision, "package_hash": package["package_hash"],
        "submission_hash": digest(solution), "submission": solution, "context": context, "state": "accepted", "applied": False,
        "created_at": datetime.now(timezone.utc).isoformat(), 'memory_mb':memory_mb})
    if attempt["binding"] != binding:
        raise SessionConflictError("This attempt ID is already bound to a different submission or exercise.")
    if attempt.get("state") == "completed":
        return attempt, False
    attempt, claimed = store.admit_attempt(user, key)
    if not claimed:
        return attempt, False
    if package.get("verification_key"):
        package["verification"] = store.get(user, package["verification_key"]) or {}
    deadline_token = _evaluation_deadline.set(time.monotonic() + 90)
    try:
        result = _evaluate(package, solution)
    except Exception:
        result = outcome("retryable_error", "Assessment is temporarily unavailable. Your submission was accepted.")
    finally:
        _evaluation_deadline.reset(deadline_token)
    result = normalize_grade_result(result, method=result.get("method", "execution"), calibrated=False)
    result["attempt_id"] = attempt_id
    token = attempt["lease_token"]
    attempt = store.complete_attempt(user, key, token, result)
    return attempt, attempt.get("state") == "completed" and attempt.get("lease_token") == token


def grade_lesson(repo, user, sid, node, solution, revision, attempt_id):
    from services.learning_service import grade, LockedNodeError
    from services.completion import locked_node_ids, incomplete_prereq_labels
    ls = repo.get(user, sid)
    if ls is None:
        raise KeyError("Session not found")
    previous = GradingStore(repo).get(user, "attempt:" + str(attempt_id))
    exercise = (ls.get("lessons", {}).get(node) or {}).get("exercise")
    legacy_unversioned = bool(exercise) and exercise.get("revision") is None
    if previous:
        if revision is None:
            revision = previous["revision"]
        package = GradingStore(repo).get(user, "package:" + previous["package_hash"])
        if package:
            exercise = {**package["public"], "package_id": package["package_id"]}
    if not exercise or not exercise.get("statement"):
        raise ValueError("Open the lesson before submitting.")
    if node not in {n["id"] for n in ls["skill_graph"]["nodes"]}:
        raise ValueError("This skill is not on the roadmap.")
    # Permit an already-applied replay after adaptation removed/locked the task.
    if not previous and node in locked_node_ids(ls["skill_graph"], ls["node_state"]):
        raise LockedNodeError(node, incomplete_prereq_labels(ls["skill_graph"], ls["node_state"], node))
    if "grading_artifact" in exercise or not exercise.get("package_id"):
        from services.learning_service import _repair_missing_grading_artifact
        with repair_context(repo, user, sid):
            _repair_missing_grading_artifact(ls, node)
        synchronize_session(repo, user, sid, ls)
        repo.save(user, sid, ls)
    if legacy_unversioned and revision is None:
        revision = exercise["revision"]
    attempt, created = accept_and_evaluate(repo, user, exercise, solution, revision, attempt_id,
        context={"session_id": sid, "node_id": node})
    result = clone(attempt.get("result") or outcome("pending", "Assessment is pending."))
    result.update(attempt_id=attempt_id, applied=attempt.get("applied", False))
    if not created:
        if attempt.get("state") == "completed" and not attempt.get("applied") and eligible_score(result) is not None:
            raise SessionConflictError("This completed assessment is saved but unapplied. Reload and explicitly reconcile the attempt.")
        return repo.get(user, sid), attempt.get("new_node_ids", []), result
    ls, added, result = grade(ls, node, solution, revision, adjudicated_result=result)
    # Even non-eligible outcomes get an atomic application marker; no mastery event.
    GradingStore(repo).apply_session(user, sid, ls, "attempt:" + attempt_id, added)
    result.update(applied=True, attempt_id=attempt_id)
    return ls, added, result


def reconcile_lesson(repo, user, sid, node, attempt_id):
    """Explicitly apply a saved terminal result against the current unchanged task."""
    from services.learning_service import grade
    store = GradingStore(repo)
    attempt = store.get(user, "attempt:" + attempt_id)
    if not attempt or attempt.get("context") != {"session_id": sid, "node_id": node}:
        raise KeyError("Attempt not found")
    ls = repo.get(user, sid)
    if ls is None:
        raise KeyError("Session not found")
    result = clone(attempt.get("result") or outcome("pending", "Assessment is pending."))
    if attempt.get("applied"):
        return ls, attempt.get("new_node_ids", []), {**result, "applied": True}
    exercise = (ls.get("lessons", {}).get(node) or {}).get("exercise") or {}
    if attempt.get("state") != "completed" or exercise.get("revision") != attempt["revision"] or exercise.get("package_id") != attempt["package_hash"]:
        raise SessionConflictError("The completed assessment no longer matches the current exercise.")
    added = []
    if eligible_score(result) is not None:
        ls, added, result = grade(ls, node, attempt["submission"], attempt["revision"], adjudicated_result=result)
    store.apply_session(user, sid, ls, "attempt:" + attempt_id, added)
    return ls, added, {**result, "applied": True, "attempt_id": attempt_id}
