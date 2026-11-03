"""Back up the existing local database, then apply the additive P2 #8 migration."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    out = ROOT / 'logs/p2-8-activation-20261030'
    out.mkdir(parents=True, exist_ok=True)
    cli = json.loads((ROOT / 'worker/host.local.json').read_text())['cli']
    details = json.loads(subprocess.check_output(cli + ['inspect', 'mindmorph-db'], text=True))[0]
    if details['Config']['Image'] != 'pgvector/pgvector:pg16': raise RuntimeError('Unexpected project database container')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = out / ('postgres-before-' + stamp + '.dump')
    result = subprocess.run(cli + ['exec', 'mindmorph-db', 'pg_dump', '-U', 'mindmorph', '-d', 'mindmorph', '-Fc'], capture_output=True, timeout=60)
    if result.returncode or not result.stdout: raise RuntimeError('Database backup failed; migration not attempted')
    backup.write_bytes(result.stdout)
    check = subprocess.run(cli + ['exec', '-i', 'mindmorph-db', 'pg_restore', '--list'], input=result.stdout, capture_output=True, timeout=30)
    if check.returncode: raise RuntimeError('Backup validation failed; migration not attempted')
    (out / (backup.stem + '-contents.txt')).write_bytes(check.stdout)
    from persistence.db import get_engine, check_ready
    from sqlalchemy import text, inspect
    def snapshot():
        with get_engine().connect() as connection:
            data = {}
            for table in inspect(get_engine()).get_table_names():
                if table == 'alembic_version':
                    data[table] = list(connection.execute(text('select version_num from alembic_version')).scalars())
                else:
                    rows = connection.execute(text('select row_to_json(t)::text from "' + table + '" t')).scalars().all()
                    data[table] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()}
            return data
    before = snapshot()
    (out / 'database-before.json').write_text(json.dumps(before, indent=2))
    migration = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT, capture_output=True, timeout=120)
    (out / 'migration.txt').write_bytes(migration.stdout + migration.stderr)
    if migration.returncode: raise RuntimeError('Migration failed; existing application has not been restarted')
    after = snapshot()
    for table, summary in before.items():
        if table != 'alembic_version' and after[table] != summary:
            raise RuntimeError('Existing database content changed unexpectedly')
    check_ready()
    (out / 'database-after.json').write_text(json.dumps(after, indent=2))
    print('Backup validated, existing rows unchanged, database migration head:', after['alembic_version'])


if __name__ == '__main__': main()
