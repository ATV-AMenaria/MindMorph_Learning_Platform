"""Real subprocess runs of the grading executor: pass / fail / timeout. Deterministic."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from tools.code_executor import execute_tests

_PASS_TESTS = "from solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n\ndef test_add_neg():\n    assert add(-1, 1) == 0\n"


def test_passing_solution_scores_full():
    result = execute_tests("def add(a, b):\n    return a + b\n", _PASS_TESTS)
    assert result["timed_out"] is False
    assert result["total"] == 2
    assert result["passed"] == 2
    assert result["score"] == 100.0
    assert result["failures"] == []


def test_failing_solution_captures_failure():
    result = execute_tests("def add(a, b):\n    return a - b\n", _PASS_TESTS)
    assert result["timed_out"] is False
    assert result["total"] == 2
    assert result["passed"] < 2
    assert result["score"] < 100.0
    assert result["failures"]  # at least one FAILED line surfaced


def test_infinite_loop_is_killed_by_timeout():
    solution = "def add(a, b):\n    while True:\n        pass\n"
    tests = "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    result = execute_tests(solution, tests, timeout=3)
    assert result["timed_out"] is True
    assert result["score"] == 0.0


def test_empty_tests_returns_zero_total():
    result = execute_tests("x = 1\n", "")
    assert result["total"] == 0
    assert result["timed_out"] is False


def test_bare_module_asserts_pass_via_fallback():
    # The LLM grader sometimes emits bare module-level asserts (no def test_*). pytest collects 0;
    # the script fallback must still execute them and report a pass.
    solution = "def add(a, b):\n    return a + b\n"
    tests = "from solution import add\nassert add(2, 3) == 5\nassert add(0, 0) == 0\n"
    result = execute_tests(solution, tests)
    assert result["total"] == 1
    assert result["passed"] == 1
    assert result["score"] == 100.0


def test_bare_module_asserts_fail_is_reported():
    # A failing bare module-level assert errors during pytest collection (exit 2, not the exit-5
    # "no tests" path). Either way the grade must report failure, never a false pass.
    solution = "def add(a, b):\n    return a - b\n"
    tests = "from solution import add\nassert add(2, 3) == 5\n"
    result = execute_tests(solution, tests)
    assert result["passed"] == 0
    assert result["score"] == 0.0


@pytest.mark.parametrize("fmt,artifact,solution,expect_key", [
    ("coding_challenge", {"unit_tests": ["from solution import add\n\ndef test_x():\n    assert add(1,1)==2\n"]},
     "def add(a,b):\n    return a+b\n", "passed"),
])
def test_grade_submission_coding(fmt, artifact, solution, expect_key):
    from agents.exercise.grader_agent import grade_submission
    result = grade_submission(fmt, solution, artifact)
    assert result is not None
    assert expect_key in result
    assert result["passed"] == 1


def test_strip_sandbox_paths_keeps_message_drops_path():
    from tools.code_executor import _strip_sandbox_paths

    raw = ("collect: ImportError: cannot import name 'prepare_and_fit' from 'solution' "
           "(/private/var/folders/lg/abc123/T/mindmorph_grade_x9/solution.py)")
    cleaned = _strip_sandbox_paths(raw)
    assert "cannot import name 'prepare_and_fit' from 'solution'" in cleaned
    assert "(solution.py)" in cleaned
    assert "/private/var" not in cleaned


def test_missing_name_failure_has_no_absolute_path():
    # End-to-end: the solution lacks the name the tests import. The learner sees the cause, not
    # the sandbox path.
    result = execute_tests("def other():\n    pass\n",
                           "from solution import prepare_and_fit\n\ndef test_x():\n    prepare_and_fit()\n")
    blob = " ".join(result["failures"]) + result["stdout"]
    assert "cannot import name 'prepare_and_fit'" in blob
    assert "mindmorph_grade_" not in blob
    assert result["score"] == 0.0


def test_grade_submission_empty_returns_none():
    from agents.exercise.grader_agent import grade_submission
    assert grade_submission("coding_challenge", "   ", {"unit_tests": ["x"]}) is None
