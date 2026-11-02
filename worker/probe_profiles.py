"""Run real isolated acceptance probes, then write a versioned local image lock.

This explicit operator action uses disposable mm-grading containers only.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from tools import isolated_runner as runner
from tools.runtime_profiles import PROFILES, manifest_hash


def main():
    evidence = Path("logs/p2-8-implementation")
    build = json.loads((evidence / "worker-build.json").read_text())
    profiles = ["python-core", "python-data", "javascript-node", "typescript-node", "sqlite"]
    candidate = {"probe_version": 1, "profiles": {key: {"image": build["image"], "revision": PROFILES[key].revision, "manifest_hash": manifest_hash(key), "actual_versions": {"candidate": "pending release probes"}, "probes_passed": True} for key in profiles}}
    candidate_path = evidence / "worker-candidate-lock.json"
    candidate_path.write_text(json.dumps(candidate))
    os.environ["MM_WORKER_LOCK"] = str(candidate_path)
    records = []
    def probe(name, profile, files, expected=None, status="succeeded", **kwargs):
        result = runner.execute_profile(profile, files, **kwargs)
        passed = result["status"] == status and (expected is None or result["stdout"] == expected)
        records.append({"name": name, "passed": passed, "result": result})
        print(name, passed, result["status"], flush=True)
        return passed
    probe("positive", "python-core", {"main.py": "print(int(input()) * 2)"}, "42\n", stdin="21\n")
    probe("negative", "python-core", {"main.py": "raise ValueError('wrong')"}, status="learner_error")
    probe("fixture", "python-core", {"main.py": "print(open('data.csv').read().strip())", "data.csv": "a,b\n1,2\n"}, "a,b\n1,2\n")
    probe("dependency-data", "python-data", {"main.py": "import numpy,pandas,scipy,sklearn; from sklearn.linear_model import LinearRegression; print(numpy.__version__,pandas.__version__,scipy.__version__,sklearn.__version__); value=LinearRegression().fit([[0],[1]],[0,2]).predict([[2]])[0]; assert abs(value-4)<1e-10; print(round(value))"}, "2.2.2 2.2.3 1.15.1 1.6.1\n4\n")
    probe("node", "javascript-node", {"main.js": "process.stdout.write(process.version+'\\n')"}, "v22.23.4\n")
    probe("typescript", "typescript-node", {"main.ts": "import {readFileSync} from 'node:fs'; const n: number = Number(readFileSync(0,'utf8')); process.stdout.write(String(n*2)+'\\n')"}, "42\n", stdin="21\n")
    probe("sqlite", "sqlite", {"main.sql": "create table x(n); insert into x values(42); select n from x;"}, "[[42]]\n")
    probe("timeout", "python-core", {"main.py": "while True: pass"}, status="timed_out", timeout=1)
    probe("memory", "python-core", {"main.py": "x = bytearray(900*1024*1024); print('escaped')"}, status="learner_error")
    probe("isolation", "python-core", {"main.py": "import os,socket\nassert os.getuid()==1000\nassert not os.path.exists('/var/run/docker.sock')\ntry:\n open('/opt/mm/worker_main.py','w').write('x')\n raise AssertionError('writable root')\nexcept OSError: pass\ntry:\n socket.create_connection(('1.1.1.1',443),timeout=1)\n raise AssertionError('network')\nexcept OSError: pass\nprint('isolated')"}, "isolated\n")
    probe("integrity-observation", "python-core", {"main.py": "print('{\"score\":100,\"passed\":true}')"}, '{"score":100,"passed":true}\n')
    probe("output-bound", "python-core", {"main.py": "print('x'*1000000)"}, status="learner_error")
    result_holder = []
    thread = threading.Thread(target=lambda: result_holder.append(runner.execute_profile("python-core", {"main.py": "while True: pass"}, run_id="probe-cancel")))
    thread.start()
    time.sleep(2)
    before = time.monotonic()
    cancelled = runner.cancel_run("probe-cancel")
    thread.join(timeout=5)
    records.append({"name": "cancellation", "passed": cancelled and not thread.is_alive() and result_holder[0]["status"] == "cancelled" and time.monotonic()-before <= 5, "result": result_holder})
    (evidence / "worker-probes.json").write_text(json.dumps(records, indent=2))
    if not all(record["passed"] for record in records):
        raise SystemExit("Worker probes failed; release lock remains unavailable")
    from worker.inventory import collect
    versions = collect(runner._prefix(), build["image"])
    for record in candidate["profiles"].values():
        record["actual_versions"] = {"python": versions["python"], "sqlite": versions["sqlite"], **versions["tools"]}
    candidate.update(build=build, evidence="logs/p2-8-implementation/worker-probes.json")
    lock_path = Path("worker/profiles.lock.json")
    previous = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    previous.setdefault("profiles", {}).update(candidate["profiles"])
    previous.update({k: v for k, v in candidate.items() if k != "profiles"})
    lock_path.write_text(json.dumps(previous, indent=2) + "\n")


if __name__ == "__main__":
    main()
