"""Bounded OCI observations adapter; no scoring or host-code fallback.

Configuration is server owned: MM_WORKER_CLI is a JSON executable prefix and
MM_WORKER_LOCK names the probe lock. Neither is accepted from learner input.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import threading
import time
import uuid

from tools.runtime_profiles import PROFILES, commands, manifest_hash, execution_contract

OUTPUT_LIMIT = 65536
_active = threading.BoundedSemaphore(2)
_admission = threading.BoundedSemaphore(10)
_guard = threading.Lock()
_runs: dict[str, tuple[str, threading.Event]] = {}
_image_checks: dict[str, tuple[float, bool]] = {}
_memory_in_use = 0


def _browser_seccomp_path():
    path = Path(__file__).resolve().parents[1] / "worker/browser-seccomp.json"
    prefix = _prefix()
    if Path(prefix[0]).stem.lower() == "wsl":
        # Docker CLI opens this file on its own host, before sending JSON to
        # the daemon; the Windows caller's path must be translated for WSL.
        return subprocess.check_output(prefix[:-1] + ["wslpath", "-a", str(path)], text=True, timeout=5).strip()
    return str(path)


class OutputLimitExceeded(Exception):
    pass


def _prefix():
    local = Path(__file__).resolve().parents[1] / "worker" / "host.local.json"
    configured = json.loads(local.read_text(encoding="utf-8")).get("cli") if local.exists() else ["docker"]
    value = json.loads(os.environ["MM_WORKER_CLI"]) if "MM_WORKER_CLI" in os.environ else configured
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ValueError("invalid server worker CLI")
    return value


def _cli(args, *, data=None, timeout=20):
    """Drain both pipes without retaining unbounded process output."""
    process = subprocess.Popen(_prefix() + args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    captured = [bytearray(), bytearray()]
    output_guard = threading.Lock()
    overflow = threading.Event()
    def drain(pipe, target):
        while chunk := pipe.read(8192):
            with output_guard:
                remaining = OUTPUT_LIMIT - sum(map(len, captured))
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    process.kill()
    threads = [threading.Thread(target=drain, args=(pipe, target), daemon=True)
               for pipe, target in zip((process.stdout, process.stderr), captured)]
    for thread in threads:
        thread.start()
    def feed():
        try:
            process.stdin.write(data)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    if data is not None:
        threading.Thread(target=feed, daemon=True).start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=1)
        for pipe in (process.stdout, process.stderr, process.stdin):
            if pipe is not None:
                pipe.close()
    if overflow.is_set():
        raise OutputLimitExceeded("Worker output exceeded 64 KiB")
    return code, *(bytes(x).decode("utf-8", "replace") for x in captured)


def _lock():
    path = Path(os.environ.get("MM_WORKER_LOCK", str(Path(__file__).resolve().parents[1] / "worker/profiles.lock.json")))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data.get("probe_version") == 1 else {}
    except (OSError, ValueError):
        return {}


def get_capabilities(profile_ids=None):
    lock = _lock().get("profiles", {})
    result = {}
    for key, profile in PROFILES.items():
        if profile_ids is not None and key not in profile_ids:
            continue
        record = lock.get(key, {})
        image = record.get("image", "")
        valid = (record.get("revision") == profile.revision and
                 re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None and
                 record.get("probes_passed") is True and
                 record.get("manifest_hash") == manifest_hash(key) and
                 isinstance(record.get("actual_versions"), dict) and bool(record["actual_versions"]))
        if valid:
            try:
                cached = _image_checks.get(image)
                if cached and time.monotonic() - cached[0] < 30:
                    valid = cached[1]
                else:
                    code, actual, _ = _cli(["image", "inspect", "--format", "{{.Id}}", image], timeout=20)
                    valid = code == 0 and actual.strip() == image
                    _image_checks[image] = (time.monotonic(), valid)
            except (OSError, ValueError, subprocess.SubprocessError):
                valid = False
        result[key] = {"available": bool(valid), "revision": profile.revision,
                       "entrypoint": profile.entrypoint, "contract": execution_contract(key),
                       "packages": list(profile.packages),
                       "reason": "" if valid else "Runtime image has not passed the versioned worker probes."}
    return result


def _archive(files, stdin):
    if not isinstance(files, dict) or not files or len(files) > 16:
        raise ValueError("Supply between 1 and 16 files")
    entries = []
    total = 0
    for name, source in files.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,127}", name)
                or any(p in ("", ".", "..") for p in name.split("/"))
                or PurePosixPath(name).is_absolute() or not isinstance(source, str)):
            raise ValueError("Invalid submission filename or content")
        content = source.encode("utf-8")
        if len(content) > 262144:
            raise ValueError("Submission file exceeds 256 KiB")
        total += len(content)
        entries.append((name, content))
    if not isinstance(stdin, str) or len(stdin.encode("utf-8")) > 262144 or total + len(stdin.encode("utf-8")) > 1048576:
        raise ValueError("Submission exceeds input limit")
    entries.append((".mm-stdin", stdin.encode("utf-8")))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in entries:
            item = tarfile.TarInfo(name)
            item.size, item.uid, item.gid, item.mode = len(content), 1000, 1000, 0o444
            archive.addfile(item, io.BytesIO(content))
    return buffer.getvalue()


def cancel_run(run_id):
    with _guard:
        record = _runs.get(run_id)
        if record is None:
            return False
        name, event = record
        event.set()
    try:
        _cli(["rm", "-f", name], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def execute_profile(profile_id, files, *, entrypoint=None, stdin="", timeout=None, run_id=None, deadline_seconds=120):
    global _memory_in_use
    started = time.monotonic()
    if type(deadline_seconds) not in (int, float) or not 0 < deadline_seconds <= 120:
        return {"status": "platform_error", "stderr": "Invalid worker deadline", "stdout": "", "run_id": run_id}
    run_id = run_id or str(uuid.uuid4())
    profile = PROFILES.get(profile_id)
    result = {"status": "unavailable", "stdout": "", "stderr": "", "exit_code": None,
              "duration": 0.0, "run_id": run_id, "profile_id": profile_id,
              "profile_revision": profile.revision if profile else None}
    def finish(status, message=""):
        result.update(status=status, duration=round(time.monotonic() - started, 3))
        if message:
            result["stderr"] = message
        return result
    try:
        payload = _archive(files, stdin)
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("Invalid run identity")
        entrypoint = entrypoint or (profile.entrypoint if profile else "main.py")
        if entrypoint not in files:
            raise ValueError("Entrypoint must name a supplied file")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30):
            raise ValueError("Invalid execution timeout")
    except ValueError as exc:
        return finish("platform_error", str(exc))
    if not profile or not get_capabilities([profile_id]).get(profile_id, {}).get("available"):
        return finish("unavailable", "The requested runtime is unavailable")
    if not _admission.acquire(blocking=False):
        return finish("unavailable", "Worker queue is full; retry later")
    name = "mm-grading-" + uuid.uuid4().hex
    event = threading.Event()
    admitted = False
    reserved_memory = False
    phase = "startup"
    with _guard:
        if run_id in _runs:
            _admission.release()
            return finish("platform_error", "Run identity is already active")
        _runs[run_id] = (name, event)
    try:
        deadline = min(time.monotonic() + 30, started + deadline_seconds)
        while not admitted and not event.is_set() and time.monotonic() < deadline:
            with _guard:
                if _memory_in_use + profile.memory_mb <= 2048:
                    admitted = _active.acquire(blocking=False)
                    if admitted:
                        _memory_in_use += profile.memory_mb
                        reserved_memory = True
            if not admitted:
                event.wait(0.1)
        if event.is_set():
            return finish("cancelled")
        if not admitted:
            return finish("unavailable", "Worker queue deadline exceeded")
        image = _lock()["profiles"][profile_id]["image"]
        budget = min(float(timeout or profile.run_seconds), profile.run_seconds)
        args = ["create", "--name", name, "--label", "mindmorph.grading=1", "--network", "none",
                "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--read-only", "--memory", f"{profile.memory_mb}m", "--memory-swap", f"{profile.memory_mb}m",
                "--cpus", str(profile.cpus), "--pids-limit", "64", "--ulimit", "nofile=1024:1024",
                "--log-driver", "none", "--ipc", "none", "--tmpfs", "/work:rw,exec,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=700",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=1000,gid=1000,mode=700",
                "--env", "HOME=/work", "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1",
                "--env", "GOCACHE=/work/.cache", "--env", "GOPATH=/work/go", "--env", "CGO_ENABLED=0",
                "--env", "GOMAXPROCS=2",
                "--entrypoint", "/bin/sleep", image, "infinity"]
        if profile_id == "react-dom":
            # Chromium retains its user-namespace sandbox. This is Playwright's
            # default Docker seccomp profile with only its documented namespace allowances.
            args[args.index("--pids-limit") + 1] = "256"
            args[args.index("--ipc") + 1] = "private"
            args[1:1] = ["--security-opt", "seccomp=" + _browser_seccomp_path(), "--shm-size", "128m"]
        def bounded_cli(arguments, **kwargs):
            remaining = deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, deadline_seconds)
            kwargs["timeout"] = min(kwargs.get("timeout", 20), remaining)
            return _cli(arguments, **kwargs)
        code, _, error = bounded_cli(args)
        if code:
            return finish("cancelled" if event.is_set() else "platform_error", "Worker container creation failed")
        if event.is_set():
            return finish("cancelled")
        code, _, _ = bounded_cli(["start", name], timeout=20)
        if code:
            return finish("cancelled" if event.is_set() else "platform_error", "Worker startup failed")
        code, _, transfer_error = bounded_cli(["exec", "-i", name, "tar", "--no-same-owner", "-xf", "-", "-C", "/work"], data=payload, timeout=10)
        if code:
            return finish("cancelled" if event.is_set() else "platform_error", "Worker input transfer failed: " + transfer_error[:1000])
        compile_command, run_command = commands(profile_id, entrypoint)
        if profile_id == "postgresql":
            code, _, _ = bounded_cli(["exec", name, "python", "-I", "/opt/mm/start_postgres.py"], timeout=20)
            if code:
                return finish("platform_error", "Private database startup failed")
        if compile_command:
            phase = "compile"
            code, stdout, stderr = bounded_cli(["exec", name] + compile_command, timeout=30)
            if event.is_set():
                return finish("cancelled")
            if code:
                result.update(stdout=stdout, stderr=stderr, exit_code=code)
                return finish("learner_error")
        if event.is_set():
            return finish("cancelled")
        phase = "run"
        code, stdout, stderr = bounded_cli(["exec", "-i", name] + run_command, data=stdin.encode("utf-8"), timeout=budget)
        result.update(stdout=stdout, stderr=stderr)
        if profile_id == "react-dom" and code == 70:
            return finish("platform_error", "The sandboxed browser could not start")
        if event.is_set():
            return finish("cancelled")
        inspect_code, state, _ = bounded_cli(["inspect", "--format", "{{json .State}}", name], timeout=5)
        if inspect_code:
            return finish("platform_error", "Worker state could not be verified")
        state = json.loads(state)
        exit_code = code
        result["exit_code"] = exit_code
        if state.get("OOMKilled"):
            result["resource_limit"] = "memory"
            return finish("learner_error", "Submission exceeded the runtime memory limit")
        if state.get("Error") or not isinstance(exit_code, int):
            return finish("platform_error", "Worker did not terminate normally")
        return finish("succeeded" if exit_code == 0 else "learner_error")
    except OutputLimitExceeded:
        result["resource_limit"] = "output"
        return finish("learner_error", "Worker output exceeded 64 KiB")
    except subprocess.TimeoutExpired:
        result["phase"] = phase
        deadline_expired = time.monotonic() - started >= deadline_seconds - .1
        return finish("cancelled" if event.is_set() else "timed_out" if phase in {"run", "compile"} and not deadline_expired else "platform_error", "Worker deadline exceeded")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return finish("platform_error", "Worker execution failed")
    finally:
        try:
            _cli(["rm", "-f", name], timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        with _guard:
            _runs.pop(run_id, None)
        if admitted:
            _active.release()
        if reserved_memory:
            with _guard:
                _memory_in_use -= profile.memory_mb
        _admission.release()
