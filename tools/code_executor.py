# Runs a learner's Python solution against generated unit tests and reports pass/fail.
#
# Uses a tiny self-contained runner (no pytest dependency in the child) so grading works in any
# Python environment. The runner imports the test module, runs every `test_*` function, and prints
# a parseable summary. Module-level `assert`s (when the grader emits bare asserts) execute at import.
#
# SECURITY — READ THIS:
#   This is a HANG-GUARD for a LOCAL, SINGLE-USER prototype. It is NOT a security sandbox.
#   It runs the submitted code in a separate subprocess with a wall-clock timeout (primary guard),
#   a fresh temp working directory, an environment scrubbed of API-key secrets, and best-effort POSIX
#   resource limits (CPU / file size). It CANNOT block network access or filesystem access outside
#   the temp dir, and memory limits (RLIMIT_AS) are unreliable on macOS. The risk is acceptable here
#   because it executes mostly the user's own code on the user's own machine.
#
#   Any hosted or multi-user deployment MUST move grading into the containerized Evaluation Service
#   (architecture §4) with real isolation (gVisor / Firecracker / a locked-down container).

import os
import re
import sys
import signal
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

try:
    import resource  # POSIX only
except ImportError:  # pragma: no cover - Windows
    resource = None

DEFAULT_TIMEOUT_SECONDS = 10
# RLIMIT_CPU counts from fork, so interpreter startup eats into this before learner code runs;
# keep it comfortably above the wall-clock timeout (which is the real guard).
_CPU_SECONDS = 15
_MAX_FILE_BYTES = 16 * 1024 * 1024  # 16 MB cap on files the solution can write
_KILL_DRAIN_SECONDS = 5  # bound the post-SIGKILL pipe drain so it can never hang the UI thread

# Substrings that mark an env var as a secret to strip before handing the env to the child.
_SECRET_MARKERS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "GROQ", "OPENROUTER",
                   "SERPER", "APIFY", "GITHUB", "OPENAI", "ANTHROPIC", "LANGCHAIN", "LANGSMITH",
                   "HUGGINGFACE", "HF_")

_SUMMARY_RE = re.compile(r"^MINDMORPH_SUMMARY\s+(\d+)\s+(\d+)\s*$", re.MULTILINE)

# Child output is shown to the learner verbatim, and Python stamps the sandbox's absolute path into
# ImportErrors and tracebacks ("cannot import name 'x' from 'solution' (/private/var/.../solution.py)").
# That path is noise to a learner and leaks local filesystem layout. Match on the temp-dir prefix
# rather than the mkdtemp string itself — mkdtemp returns /var/folders/... while Python reports the
# resolved /private/var/folders/... — and keep only the filename.
_SANDBOX_PATH_RE = re.compile(r"(?:/[^/\s'\"()<>,]+)*/mindmorph_grade_[^/\s'\"()<>,]*(?:/[^/\s'\"()<>,]+)*")

# Self-contained runner written into the temp dir. No third-party imports — works without pytest.
#
# The runner splits failures into two kinds, because they mean opposite things:
#   MINDMORPH_FAILURE — the learner's solution is wrong (assertion failed, missing name, bad value).
#   MINDMORPH_HARNESS — the GENERATED TEST cannot run at all, whatever the learner submitted
#                       (imports a library this interpreter lacks, uses an undeclared name, opens a
#                       data file that does not exist). That is a server-side defect and must never
#                       be scored as learner knowledge.
_RUNNER_SRC = '''\
import importlib, os, sys, traceback


def _harness_reason(e, tb):
    """Return why this failure is the harness's fault, or None if it is a real learner failure.

    The sandbox contains exactly three files: solution.py, test_solution.py, _runner.py.
      - ImportError for any module other than `solution` => the grading environment is missing a
        dependency the generated test assumed. (`solution` itself IS a learner signal: the learner
        did not define the name the exercise asked for.)
      - NameError raised inside test_solution.py => the test module used a name it never imported.
      - SyntaxError in test_solution.py => the generated test is not valid Python.
      - FileNotFoundError, whatever the frame => the test invented a data path nobody created. This
        one is deliberately frame-INDEPENDENT: the open usually happens inside the learner's own
        function, called with a path the test made up, so a deepest-frame test would misblame them.
    """
    if isinstance(e, SyntaxError):
        if e.filename and os.path.basename(e.filename) == "test_solution.py":
            return "the generated test module is not valid Python"
        return None
    if isinstance(e, ImportError):
        name = getattr(e, "name", None)
        if name != "solution":
            return "the grading environment has no module %r" % (name or "?",)
        return None
    if isinstance(e, NameError):
        frames = traceback.extract_tb(tb)
        if frames and os.path.basename(frames[-1].filename) == "test_solution.py":
            return "the generated test uses a name it never imported"
        return None
    if isinstance(e, FileNotFoundError):
        return "the generated test expects a data file (%s) that does not exist" % (e.filename or "?",)
    return None


passed, failures, harness = 0, [], []
try:
    mod = importlib.import_module("test_solution")
except BaseException as e:
    # Import-time failure: bad import (e.g. solution missing the required name), a failing
    # module-level assert, or a broken test module.
    # One line per failure: the summary parser is line-based, and a multi-line exception rendering
    # (SyntaxError shows source + caret) would otherwise lose everything after the first line.
    msg = " ".join("".join(traceback.format_exception_only(type(e), e)).split())
    reason = _harness_reason(e, e.__traceback__)
    print(("MINDMORPH_HARNESS " if reason else "MINDMORPH_FAILURE ")
          + "collect: " + msg + (" [" + reason + "]" if reason else ""))
    print("MINDMORPH_SUMMARY 0 0")
    sys.exit(0)

tests = [(n, getattr(mod, n)) for n in dir(mod) if n.startswith("test") and callable(getattr(mod, n))]
if not tests:
    # No test functions, but the module imported cleanly => any bare module-level asserts passed.
    print("MINDMORPH_SUMMARY 1 1")
    sys.exit(0)

for name, fn in tests:
    try:
        fn()
        passed += 1
    except BaseException as e:
        msg = " ".join("".join(traceback.format_exception_only(type(e), e)).split())
        reason = _harness_reason(e, e.__traceback__)
        if reason:
            harness.append(name + ": " + msg + " [" + reason + "]")
        else:
            failures.append(name + ": " + msg)

for h in harness:
    print("MINDMORPH_HARNESS " + h)
for f in failures:
    print("MINDMORPH_FAILURE " + f)
print("MINDMORPH_SUMMARY %d %d" % (passed, len(tests)))
'''


def _apply_resource_limits():  # pragma: no cover - runs in the child process
    """Best-effort defense-in-depth in the child before exec. Never raise (would kill the child)."""
    if resource is None:
        return
    for limit, value in ((resource.RLIMIT_CPU, _CPU_SECONDS), (resource.RLIMIT_FSIZE, _MAX_FILE_BYTES)):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            # Platform refused this limit (e.g. macOS quirks) — skip it; timeout remains the guard.
            pass


def _child_env(tmp: str) -> Dict[str, str]:
    """Inherit the parent env (so the interpreter finds its own stdlib/site-packages) but strip
    every API-key / token secret, and point PYTHONPATH at the temp dir so `import solution` works."""
    env = {k: v for k, v in os.environ.items()
           if not any(marker in k.upper() for marker in _SECRET_MARKERS)}
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = tmp + (os.pathsep + existing_pp if existing_pp else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _strip_sandbox_paths(output: str) -> str:
    """Relativise absolute sandbox paths in child output down to bare filenames.

    Keeps the useful part of the message ("cannot import name 'x' from 'solution'") and drops the
    temp-dir path. Applied once to the raw child output, so every derived field inherits it.
    """
    return _SANDBOX_PATH_RE.sub(lambda m: os.path.basename(m.group(0)), output or "")


def _parse_summary(output: str):
    """Return (passed, total) from the runner's summary line, or None if absent."""
    m = _SUMMARY_RE.search(output or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _extract_failures(output: str) -> List[str]:
    """The runner's per-test failure lines."""
    return [ln[len("MINDMORPH_FAILURE "):].strip()
            for ln in (output or "").splitlines() if ln.startswith("MINDMORPH_FAILURE ")]


def _extract_harness(output: str) -> List[str]:
    """The runner's harness-error lines (broken generated test, not a learner failure)."""
    return [ln[len("MINDMORPH_HARNESS "):].strip()
            for ln in (output or "").splitlines() if ln.startswith("MINDMORPH_HARNESS ")]


def _harness_result(reasons: List[str], stdout: str = "") -> Dict[str, Any]:
    """A verdict of "we could not grade this" — deliberately carries NO score.

    Callers must check ``harness_error`` before reading a score: a broken generated test says
    nothing about the learner, so scoring it 0 would fabricate a knowledge gap (see
    services/mastery.apply_score, which refuses to record these).
    """
    return {"harness_error": True, "failures": reasons, "passed": 0, "total": 0,
            "stdout": (stdout or "")[-4000:], "timed_out": False}


def execute_tests(
    solution_code: str,
    test_code: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Run `test_code` against `solution_code` in an isolated subprocess (no pytest dependency).

    The tests import the solution as `import solution` / `from solution import ...`.
    Returns: {passed, total, failures, score, stdout, timed_out}. Never raises on test failure.
    """
    if not test_code or not test_code.strip():
        # No tests => nothing was measured. Scoring this 0 would have marked the learner as failing
        # an exercise that was never graded.
        return _harness_result(["No unit tests were generated for this exercise."])

    tmp = tempfile.mkdtemp(prefix="mindmorph_grade_")
    try:
        with open(os.path.join(tmp, "solution.py"), "w") as f:
            f.write(solution_code or "")
        with open(os.path.join(tmp, "test_solution.py"), "w") as f:
            f.write(test_code)
        with open(os.path.join(tmp, "_runner.py"), "w") as f:
            f.write(_RUNNER_SRC)

        env = _child_env(tmp)
        stdout, _returncode, timed_out = _run([sys.executable, "_runner.py"], tmp, env, timeout)
        # Clean before parsing/truncating so failures, stdout and the [-4000:] slice all inherit it.
        stdout = _strip_sandbox_paths(stdout)
        if timed_out:
            return {"passed": 0, "total": 0, "failures": ["Execution timed out (possible infinite loop)."],
                    "score": 0.0, "stdout": (stdout or "")[-4000:], "timed_out": True}

        harness = _extract_harness(stdout)
        if harness:
            # The generated test cannot run at all — a server-side defect, not a learner failure.
            return _harness_result(harness, stdout)

        parsed = _parse_summary(stdout or "")
        if parsed is None:
            # Runner never printed a summary (it crashed or was killed). We have no measurement, so
            # this is not a learner signal either.
            return _harness_result(
                _extract_failures(stdout) or ["The grading runner could not execute the tests."], stdout)

        passed, total = parsed
        score = (passed / total * 100.0) if total else 0.0
        return {
            "passed": passed,
            "total": total,
            "failures": _extract_failures(stdout),
            "score": score,
            "stdout": (stdout or "")[-4000:],
            "timed_out": False,
        }
    finally:
        _rmtree_quiet(tmp)

# Generated tests must be self-contained: the sandbox holds only solution.py, so a test that reads a
# data file can never pass. Catch that statically — a stub dry run cannot, since the open happens
# inside the (stubbed-out) learner function.
_FILE_IO_RE = re.compile(
    r"\bopen\s*\(|\bread_csv\b|\bread_json\b|\bread_excel\b|\bread_parquet\b|\bnp\.load\b"
    r"|['\"][^'\"]*\.(?:csv|json|txt|parquet|xlsx|npy)['\"]"
)

# Module-level __getattr__ satisfies both `import solution` and `from solution import anything`, so a
# dry run exercises the TEST module's own imports/names without needing a reference implementation.
_STUB_SOLUTION = "def __getattr__(name):\n    return lambda *a, **k: None\n"


def check_test_artifact(unit_tests: List[str]) -> Optional[str]:
    """Validate a freshly generated test module. Returns a reason it can never run, or None if OK.

    Runs the tests against a stub solution: every remaining error is the test module's own fault
    (missing library, undeclared import, syntax error). Assertion failures against the stub are
    expected and ignored.
    """
    src = "\n".join(unit_tests or [])
    if not src.strip():
        return "no unit tests were generated"
    m = _FILE_IO_RE.search(src)
    if m:
        return f"tests read an external file ({m.group(0)!r}); the sandbox has no data files"
    result = execute_tests(_STUB_SOLUTION, src)
    if result.get("harness_error"):
        return "; ".join(result.get("failures") or ["tests could not run"])
    return None


# =============================================================================
# JavaScript execution harness (Node.js built-in node:test + node:assert)
# =============================================================================

# Self-contained Node.js runner — mirrors the Python _RUNNER_SRC protocol.
# Uses CommonJS require so tests can `require('./solution')`. Runs every function whose name starts
# with `test` exported from `test_solution.js`, or delegates to `node:test` describe/it blocks.
_JS_RUNNER_SRC = r'''
const path = require('path');
const fs = require('fs');

let passed = 0;
const failures = [];
const harness = [];

function harnessReason(e) {
  const msg = String(e.message || e);
  // MODULE_NOT_FOUND for anything other than ./solution = harness fault
  if (e.code === 'MODULE_NOT_FOUND' && !msg.includes('./solution')) {
    return 'the grading environment has no module: ' + msg;
  }
  // SyntaxError in test file = harness fault
  if (e instanceof SyntaxError && e.stack && e.stack.includes('test_solution.js') && !e.stack.includes('solution.js:')) {
    return 'the generated test module is not valid JavaScript';
  }
  // ReferenceError in test file = harness fault (undeclared name)
  if (e instanceof ReferenceError && e.stack && e.stack.includes('test_solution.js') && !e.stack.includes('solution.js:')) {
    return 'the generated test uses an undeclared name';
  }
  return null;
}

// Try to load the test module
let testMod;
try {
  testMod = require('./test_solution');
} catch (e) {
  const reason = harnessReason(e);
  const oneLine = String(e.message || e).replace(/\n/g, ' ');
  console.log((reason ? 'MINDMORPH_HARNESS ' : 'MINDMORPH_FAILURE ') +
              'collect: ' + oneLine + (reason ? ' [' + reason + ']' : ''));
  console.log('MINDMORPH_SUMMARY 0 0');
  process.exit(0);
}

// Discover test_* functions from exports
const tests = Object.entries(testMod || {}).filter(
  ([name, fn]) => name.startsWith('test') && typeof fn === 'function'
);

if (tests.length === 0) {
  // No exported test_* functions — the test may use describe/it blocks via node:test.
  // Run the test module as a node:test script instead.
  const { execSync } = require('child_process');
  try {
    const out = execSync(
      '"' + process.execPath + '" --test test_solution.js',
      { cwd: __dirname, timeout: 8000, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
    );
    // Parse node:test TAP output
    const passMatch = out.match(/# pass (\d+)/);
    const failMatch = out.match(/# fail (\d+)/);
    const p = passMatch ? parseInt(passMatch[1]) : 0;
    const f = failMatch ? parseInt(failMatch[1]) : 0;
    const total = p + f;
    if (f > 0) {
      // Extract failure details from TAP
      const failLines = out.split('\n').filter(l => l.trim().startsWith('not ok'));
      for (const line of failLines) {
        console.log('MINDMORPH_FAILURE ' + line.trim());
      }
    }
    console.log('MINDMORPH_SUMMARY ' + p + ' ' + total);
  } catch (runErr) {
    const stderr = String(runErr.stderr || runErr.stdout || runErr.message || '');
    const reason = harnessReason(runErr);
    if (reason) {
      console.log('MINDMORPH_HARNESS ' + stderr.replace(/\n/g, ' ').slice(0, 500) + ' [' + reason + ']');
    } else {
      // Parse TAP from failed run
      const output = String(runErr.stdout || '');
      const passMatch = output.match(/# pass (\d+)/);
      const failMatch = output.match(/# fail (\d+)/);
      const p = passMatch ? parseInt(passMatch[1]) : 0;
      const f = failMatch ? parseInt(failMatch[1]) : 0;
      if (p + f > 0) {
        const failLines = output.split('\n').filter(l => l.trim().startsWith('not ok'));
        for (const line of failLines) {
          console.log('MINDMORPH_FAILURE ' + line.trim());
        }
        console.log('MINDMORPH_SUMMARY ' + p + ' ' + (p + f));
      } else {
        console.log('MINDMORPH_FAILURE ' + stderr.replace(/\n/g, ' ').slice(0, 500));
        console.log('MINDMORPH_SUMMARY 0 0');
      }
    }
  }
  process.exit(0);
}

// Run exported test_* functions directly
for (const [name, fn] of tests) {
  try {
    fn();
    passed++;
  } catch (e) {
    const oneLine = String(e.message || e).replace(/\n/g, ' ');
    const reason = harnessReason(e);
    if (reason) {
      harness.push(name + ': ' + oneLine + ' [' + reason + ']');
    } else {
      failures.push(name + ': ' + oneLine);
    }
  }
}

for (const h of harness) console.log('MINDMORPH_HARNESS ' + h);
for (const f of failures) console.log('MINDMORPH_FAILURE ' + f);
console.log('MINDMORPH_SUMMARY ' + passed + ' ' + tests.length);
'''

# Stub JS solution: a Proxy that returns a no-op function for any property access.
_STUB_JS_SOLUTION = (
    "module.exports = new Proxy({}, { get: (_, name) => (...args) => undefined });\n"
)


def _child_env_js(tmp: str) -> Dict[str, str]:
    """Like _child_env() but sets NODE_PATH instead of PYTHONPATH for Node.js."""
    env = {k: v for k, v in os.environ.items()
           if not any(marker in k.upper() for marker in _SECRET_MARKERS)}
    env["NODE_PATH"] = tmp
    return env


def execute_javascript(
    solution_code: str,
    test_code: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Run JavaScript tests against learner code using Node.js. Same return shape as execute_tests."""
    if not test_code or not test_code.strip():
        return _harness_result(["No unit tests were generated for this exercise."])

    # Find node executable
    node_cmd = "node"
    if os.name == "nt":
        # On Windows, shutil.which handles .cmd/.exe resolution
        import shutil
        found = shutil.which("node")
        if found:
            node_cmd = found
        else:
            return _harness_result(["Node.js is not installed — cannot grade JavaScript exercises."])

    tmp = tempfile.mkdtemp(prefix="mindmorph_grade_js_")
    try:
        with open(os.path.join(tmp, "solution.js"), "w") as f:
            f.write(solution_code or "")
        with open(os.path.join(tmp, "test_solution.js"), "w") as f:
            f.write(test_code)
        with open(os.path.join(tmp, "_runner.js"), "w") as f:
            f.write(_JS_RUNNER_SRC)

        env = _child_env_js(tmp)
        stdout, _returncode, timed_out = _run([node_cmd, "_runner.js"], tmp, env, timeout)
        stdout = _strip_sandbox_paths(stdout)
        if timed_out:
            return {"passed": 0, "total": 0, "failures": ["Execution timed out (possible infinite loop)."],
                    "score": 0.0, "stdout": (stdout or "")[-4000:], "timed_out": True}

        harness = _extract_harness(stdout)
        if harness:
            return _harness_result(harness, stdout)

        parsed = _parse_summary(stdout or "")
        if parsed is None:
            return _harness_result(
                _extract_failures(stdout) or ["The grading runner could not execute the tests."], stdout)

        passed, total = parsed
        score = (passed / total * 100.0) if total else 0.0
        return {
            "passed": passed,
            "total": total,
            "failures": _extract_failures(stdout),
            "score": score,
            "stdout": (stdout or "")[-4000:],
            "timed_out": False,
        }
    finally:
        _rmtree_quiet(tmp)


def check_test_artifact_js(unit_tests: List[str]) -> Optional[str]:
    """Validate a freshly generated JS test module. Returns a reason it can never run, or None."""
    src = "\n".join(unit_tests or [])
    if not src.strip():
        return "no unit tests were generated"
    result = execute_javascript(_STUB_JS_SOLUTION, src)
    if result.get("harness_error"):
        return "; ".join(result.get("failures") or ["tests could not run"])
    return None


# =============================================================================
# SQL execution harness (Python sqlite3 stdlib)
# =============================================================================

_SQL_RUNNER_SRC = r'''
import json, sqlite3, sys, traceback

passed, failures, harness = 0, [], []

# Read inputs
with open("solution.sql") as f:
    solution_sql = f.read()
with open("test_assertions.json") as f:
    raw_assertions = f.read()

# Parse assertions
try:
    assertions = json.loads(raw_assertions)
    if not isinstance(assertions, list):
        assertions = [assertions]
except (json.JSONDecodeError, ValueError) as e:
    print("MINDMORPH_HARNESS collect: invalid assertion JSON [the generated test assertions are malformed]")
    print("MINDMORPH_SUMMARY 0 0")
    sys.exit(0)

# Execute the learner's SQL
conn = sqlite3.connect(":memory:")
try:
    conn.executescript(solution_sql)
except Exception as e:
    msg = " ".join(str(e).split())
    print("MINDMORPH_FAILURE collect: Solution SQL error: " + msg)
    print("MINDMORPH_SUMMARY 0 " + str(len(assertions)))
    sys.exit(0)

# Run each assertion
for i, assertion in enumerate(assertions):
    label = assertion.get("label", f"test_{i+1}")
    query = assertion.get("query", "")
    expected = assertion.get("expected", [])
    try:
        cursor = conn.execute(query)
        actual = [list(row) for row in cursor.fetchall()]
        if actual == expected:
            passed += 1
        else:
            failures.append(
                label + ": expected " + json.dumps(expected) + " but got " + json.dumps(actual)
            )
    except Exception as e:
        msg = " ".join(str(e).split())
        # Syntax error in the TEST query = harness fault
        if "syntax error" in msg.lower() or "no such" in msg.lower():
            harness.append(label + ": " + msg + " [the generated test query is invalid SQL]")
        else:
            failures.append(label + ": SQL error: " + msg)

conn.close()

for h in harness:
    print("MINDMORPH_HARNESS " + h)
for f in failures:
    print("MINDMORPH_FAILURE " + f)
print("MINDMORPH_SUMMARY %d %d" % (passed, len(assertions)))
'''


def execute_sql(
    solution_sql: str,
    test_assertions_json: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Run SQL assertions against learner's SQL using SQLite. Same return shape as execute_tests."""
    if not test_assertions_json or not test_assertions_json.strip():
        return _harness_result(["No SQL test assertions were generated for this exercise."])

    tmp = tempfile.mkdtemp(prefix="mindmorph_grade_sql_")
    try:
        with open(os.path.join(tmp, "solution.sql"), "w") as f:
            f.write(solution_sql or "")
        with open(os.path.join(tmp, "test_assertions.json"), "w") as f:
            f.write(test_assertions_json)
        with open(os.path.join(tmp, "_sql_runner.py"), "w") as f:
            f.write(_SQL_RUNNER_SRC)

        env = _child_env(tmp)
        stdout, _returncode, timed_out = _run([sys.executable, "_sql_runner.py"], tmp, env, timeout)
        stdout = _strip_sandbox_paths(stdout)
        if timed_out:
            return {"passed": 0, "total": 0, "failures": ["Execution timed out."],
                    "score": 0.0, "stdout": (stdout or "")[-4000:], "timed_out": True}

        harness = _extract_harness(stdout)
        if harness:
            return _harness_result(harness, stdout)

        parsed = _parse_summary(stdout or "")
        if parsed is None:
            return _harness_result(
                _extract_failures(stdout) or ["The grading runner could not execute the tests."], stdout)

        passed, total = parsed
        score = (passed / total * 100.0) if total else 0.0
        return {
            "passed": passed,
            "total": total,
            "failures": _extract_failures(stdout),
            "score": score,
            "stdout": (stdout or "")[-4000:],
            "timed_out": False,
        }
    finally:
        _rmtree_quiet(tmp)


def check_test_artifact_sql(sql_assertions: List[str]) -> Optional[str]:
    """Validate freshly generated SQL test assertions. Returns a reason they can never run, or None."""
    import json as _json
    raw = "\n".join(sql_assertions or [])
    if not raw.strip():
        return "no SQL assertions were generated"
    # Validate that each element is valid JSON
    for i, element in enumerate(sql_assertions):
        try:
            obj = _json.loads(element)
            if not isinstance(obj, dict):
                return f"assertion {i+1} is not a JSON object"
            if "query" not in obj or "expected" not in obj:
                return f"assertion {i+1} is missing 'query' or 'expected' keys"
        except (ValueError, _json.JSONDecodeError):
            return f"assertion {i+1} is not valid JSON"
    return None


# =============================================================================
# Unified exercise dispatcher
# =============================================================================

def execute_exercise(
    language: str,
    solution_code: str,
    test_code: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Unified entry point: routes to the right executor based on language."""
    if language == "javascript":
        return execute_javascript(solution_code, test_code, timeout)
    elif language == "sql":
        return execute_sql(solution_code, test_code, timeout)
    else:
        return execute_tests(solution_code, test_code, timeout)


def _run(cmd, cwd, env, timeout):
    """Run `cmd` in an isolated subprocess. Returns (stdout, returncode, timed_out)."""
    popen_kwargs: Dict[str, Any] = dict(
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    if os.name == "posix":
        # New session => own process group, so a timeout can kill grandchildren too.
        popen_kwargs["start_new_session"] = True
        popen_kwargs["preexec_fn"] = _apply_resource_limits

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return stdout, proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        # Bound the drain: the wall-clock guard already fired, so never wait unbounded here.
        try:
            stdout, _ = proc.communicate(timeout=_KILL_DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            stdout = ""
        return stdout, proc.returncode, True


def _kill_process_tree(proc: "subprocess.Popen"):
    try:
        if os.name == "posix":
            # start_new_session=True makes the child a session/group leader, so pgid == pid.
            # Use proc.pid directly to avoid an os.getpgid() race if the child already exited.
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _rmtree_quiet(path: str):
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    demo_solution = "def add(a, b):\n    return a + b\n"
    demo_tests = "from solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    print("=== Python ===")
    print(execute_tests(demo_solution, demo_tests))

    demo_js_solution = "function add(a, b) { return a + b; }\nmodule.exports = { add };\n"
    demo_js_tests = (
        "const assert = require('node:assert/strict');\n"
        "const { add } = require('./solution');\n\n"
        "function test_add() { assert.strictEqual(add(2, 3), 5); }\n"
        "module.exports = { test_add };\n"
    )
    print("\n=== JavaScript ===")
    print(execute_javascript(demo_js_solution, demo_js_tests))

    demo_sql = "CREATE TABLE t(x INT);\nINSERT INTO t VALUES (1),(2),(3);\n"
    demo_sql_tests = '{"label":"count","query":"SELECT COUNT(*) FROM t","expected":[[3]]}'
    print("\n=== SQL ===")
    print(execute_sql(demo_sql, demo_sql_tests))

