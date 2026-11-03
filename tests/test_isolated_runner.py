import json
import subprocess
import threading
import time

import pytest

from tools import isolated_runner as runner


@pytest.fixture
def fake_cli(monkeypatch):
    calls = []
    image = "sha256:" + "a" * 64
    monkeypatch.setattr(runner, "get_capabilities", lambda *args: {"python-core": {"available": True}})
    monkeypatch.setattr(runner, "_lock", lambda: {"profiles": {"python-core": {"image": image}}})
    def cli(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "inspect":
            return 0, json.dumps({"ExitCode": 0}), ""
        return 0, "ok\n" if args[:2] == ["exec", "-i"] else "", ""
    monkeypatch.setattr(runner, "_cli", cli)
    return calls


def test_fixed_security_flags_and_no_bind_or_shell(fake_cli):
    result = runner.execute_profile("python-core", {"main.py": "print('ok')"})
    assert result["status"] == "succeeded"
    args = fake_cli[0][0]
    for flag in ("--network", "--user", "--cap-drop", "--security-opt", "--read-only", "--memory", "--memory-swap", "--cpus", "--pids-limit", "--log-driver"):
        assert flag in args
    assert "none" in args and "ALL" in args and "1000:1000" in args
    assert not any(x in args for x in ("-v", "--volume", "--mount", "--privileged"))
    assert fake_cli[-1][0][:2] == ["rm", "-f"]
    assert "score" not in result


@pytest.mark.parametrize("name", ["../escape.py", "/root/x", "x/../../x", "a//b", "x\\y", "-x", ".mm-stdin", "x/./a"])
def test_reject_paths_before_worker(name, fake_cli):
    result = runner.execute_profile("python-core", {name: "x"}, entrypoint=name)
    assert result["status"] == "platform_error"
    assert fake_cli == []


def test_reject_sizes_and_entrypoint(fake_cli):
    assert runner.execute_profile("python-core", {"main.py": "x" * 262145})["status"] == "platform_error"
    assert runner.execute_profile("python-core", {"main.py": "pass"}, entrypoint="other.py")["status"] == "platform_error"
    assert runner.execute_profile("python-core", {"main.py": "pass"}, timeout=float("nan"))["status"] == "platform_error"


def test_timeout_always_cleans(fake_cli, monkeypatch):
    original = runner._cli
    def cli(args, **kwargs):
        if args[:2] == ["exec", "-i"] and "tar" not in args:
            raise subprocess.TimeoutExpired(args, 1)
        return original(args, **kwargs)
    monkeypatch.setattr(runner, "_cli", cli)
    assert runner.execute_profile("python-core", {"main.py": "while True: pass"})["status"] == "timed_out"
    assert fake_cli[-1][0][:2] == ["rm", "-f"]


def test_manifest_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MM_WORKER_LOCK", str(tmp_path / "absent.json"))
    assert not any(x["available"] for x in runner.get_capabilities().values())


def test_cancel_unknown_cannot_remove_container(fake_cli):
    assert runner.cancel_run("other-container") is False
    assert not fake_cli


def test_duplicate_run_id_never_touches_existing_run(fake_cli):
    runner._runs["same"] = ("mm-grading-owned", threading.Event())
    try:
        assert runner.execute_profile("python-core", {"main.py": "pass"}, run_id="same")["status"] == "platform_error"
        assert not fake_cli
    finally:
        runner._runs.pop("same")


def test_bounded_cli_output():
    # Exercise pipe draining against a real harmless process, not Docker.
    import sys
    from unittest.mock import patch
    with patch.object(runner, "_prefix", return_value=[sys.executable, "-c", "import sys; sys.stdout.write('a'*1000000); sys.stderr.write('b'*1000000)"]):
        with pytest.raises(runner.OutputLimitExceeded):
            runner._cli([], timeout=5)
