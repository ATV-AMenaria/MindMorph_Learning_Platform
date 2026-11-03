"""Immutable in-image dispatcher. Accepts observations only, never scores."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    profile, entry, budget = sys.argv[1:]
    os.chdir("/work")
    shutil.copytree("/input", "/work", dirs_exist_ok=True)
    stdin = Path(".mm-stdin").read_bytes()
    commands = {
        "python-core": ["python", "-I", entry],
        "python-data": ["python", "-I", entry],
        "javascript-node": ["node", entry],
        "react-dom": ["node", entry],
        "typescript-node": ["node", "out/" + str(Path(entry).with_suffix(".js"))],
        "sqlite": ["python", "-I", "/opt/mm/sqlite_main.py", entry],
        "c": ["/work/program"],
        "cpp": ["/work/program"],
        "java": ["java", "-XX:MaxRAMPercentage=60", "-XX:ActiveProcessorCount=2", "-cp", "out", Path(entry).stem],
        "csharp": ["mono", "/work/program.exe"],
        "go": ["/work/program"],
        "rust": ["/work/program"],
        "r": ["Rscript", "--vanilla", entry],
        "postgresql": ["psql", "--no-psqlrc", "--host", "/work", "--username", "worker", "--dbname", "postgres", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", "--file", entry],
    }
    if profile not in commands:
        return 125
    try:
        os.environ.update(GOCACHE="/work/.cache", GOPATH="/work/go", CGO_ENABLED="0")
        compilers = {
            "typescript-node": ["tsc", "--outDir", "out", "--target", "ES2022", "--module", "commonjs", entry],
            "c": ["gcc", "-O0", entry, "-o", "/work/program"],
            "cpp": ["g++", "-O0", entry, "-o", "/work/program"],
            "java": ["javac", "-J-XX:MaxRAMPercentage=60", "-d", "out", entry],
            "csharp": ["mcs", "-out:/work/program.exe", entry],
            "go": ["go", "build", "-o", "/work/program", entry],
            "rust": ["rustc", entry, "-o", "/work/program"],
        }
        if profile in compilers:
            compiled = subprocess.run(compilers[profile], timeout=30)
            if compiled.returncode:
                return compiled.returncode
        if profile == "postgresql":
            # Private per-job cluster, no TCP listener, no shared state or credentials.
            bindir = next(Path("/usr/lib/postgresql").glob("*/bin"))
            initialized = subprocess.run([str(bindir / "initdb"), "-D", "/work/pgdata", "--username=worker", "--auth=trust", "--no-locale"], stdout=subprocess.DEVNULL, timeout=15)
            if initialized.returncode:
                return 125
            database = subprocess.Popen([str(bindir / "postgres"), "-D", "/work/pgdata", "-k", "/work", "-h", "", "-c", "shared_buffers=16MB", "-c", "max_connections=5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if subprocess.run(["pg_isready", "-h", "/work", "-U", "worker"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(.05)
            else:
                return 125
        # The host observes the execution deadline. A learner-chosen exit code
        # cannot impersonate a timeout or a trusted grade status.
        return subprocess.run(commands[profile], input=stdin).returncode
    except subprocess.TimeoutExpired:
        return 124


if __name__ == "__main__":
    sys.exit(main())
