"""Provision and remove only an owned disposable PostgreSQL acceptance service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import ipaddress


def main():
    out = Path("logs/p2-8-implementation")
    out.mkdir(parents=True, exist_ok=True)
    cli = json.loads(Path("worker/host.local.json").read_text())["cli"]
    # Keep WSL active while Windows Python imports the app and tests. A detached
    # Docker daemon alone does not keep every Windows/WSL installation awake.
    keepalive = subprocess.Popen(cli[:-1] + ["sleep", "900"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL) if cli[0] == "wsl" else None
    token = uuid.uuid4().hex[:12]
    name = "mindmorph-remaining-tests-p2-8-" + token
    port = 55438
    host = subprocess.check_output(cli[:-1] + ["hostname", "-I"], text=True).split()[0] if cli[0] == "wsl" else "127.0.0.1"
    if not ipaddress.ip_address(host).is_private:
        raise RuntimeError("Verification database must bind a private local address")
    env = {**os.environ, "DATABASE_URL": f"postgresql+psycopg://p28:p28@{host}:{port}/grading_verify?connect_timeout=3",
           "MINDMORPH_STORE": "postgres", "MINDMORPH_REQUIRE_GRADING_DB": "1", "MINDMORPH_REQUIRE_DB_TESTS": "1",
           "KAFKA_BOOTSTRAP_SERVERS": "", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
           "MINDMORPH_WEB_RESEARCH": "0", "MINDMORPH_NEO4J_MIRROR": "0"}
    def run(args, log=None):
        result = subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
        if log: (out / log).write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode: raise RuntimeError((result.stdout + result.stderr)[-2500:])
        return result.stdout.strip()
    created = False
    try:
        run(cli + ["run", "-d", "--name", name, "--label", "mindmorph.p2-8=" + token,
                   "--memory", "512m", "--cpus", "1", "-p", f"{host}:{port}:5432",
                   "-e", "POSTGRES_USER=p28", "-e", "POSTGRES_PASSWORD=p28", "-e", "POSTGRES_DB=grading_verify", "pgvector/pgvector:pg16"])
        created = True
        import psycopg
        for _ in range(80):
            try:
                psycopg.connect(env["DATABASE_URL"].replace("postgresql+psycopg", "postgresql"), connect_timeout=2).close()
                break
            except psycopg.Error:
                pass
            time.sleep(.25)
        run([sys.executable, "-m", "alembic", "upgrade", "0005_grounding_provenance"], "grading-migrate-before.txt")
        # Parameter binding avoids shell/SQL quoting of legacy JSON.
        seed = "import os,psycopg; from psycopg.types.json import Jsonb; c=psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg','postgresql')); c.execute('INSERT INTO learning_sessions(user_id,session_id,data,title,revision) VALUES(%s,%s,%s,%s,%s)',('legacy-p28','old',Jsonb({'lessons':{'old':{'content':'retain'}}}),'legacy',7)); c.commit()"
        run([sys.executable, "-c", seed])
        run([sys.executable, "-m", "alembic", "upgrade", "head"], "grading-migrate-after.txt")
        check = "import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg','postgresql')); assert c.execute('SELECT data,revision FROM learning_sessions WHERE user_id=%s',('legacy-p28',)).fetchone()==({'lessons':{'old':{'content':'retain'}}},7)"
        run([sys.executable, "-c", check])
        run([sys.executable, "-m", "pytest", "tests/test_grading_storage.py", "-q", "--basetemp=" + str(out / "pytest-db-tmp"), "--junitxml=" + str(out / "grading-storage.xml")], "grading-storage.txt")
        print("PostgreSQL upgrade preservation, fresh-process replay, conflict reconciliation and lease fencing passed.")
    finally:
        try:
            if created:
                actual = run(cli + ["inspect", "--format", '{{index .Config.Labels "mindmorph.p2-8"}}', name])
                if actual != token: raise RuntimeError("Refusing to remove a container without matching task ownership")
                run(cli + ["rm", "-f", "-v", name])
        finally:
            if keepalive:
                keepalive.terminate()


if __name__ == "__main__":
    main()
