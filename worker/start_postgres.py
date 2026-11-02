"""Trusted per-job PostgreSQL bootstrap. No listener beyond a private Unix socket."""
from pathlib import Path
import subprocess
import time

bindir = next(Path("/usr/lib/postgresql").glob("*/bin"))
subprocess.run([str(bindir / "initdb"), "-D", "/work/pgdata", "--username=worker", "--auth=trust", "--no-locale"], stdout=subprocess.DEVNULL, check=True, timeout=15)
subprocess.Popen([str(bindir / "postgres"), "-D", "/work/pgdata", "-k", "/work", "-h", "", "-c", "shared_buffers=16MB", "-c", "max_connections=5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
for _ in range(100):
    if subprocess.run(["pg_isready", "-h", "/work", "-U", "worker"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        break
    time.sleep(.05)
else:
    raise SystemExit("PostgreSQL startup failed")
