"""Local API launcher that keeps the configured WSL worker/database host awake."""
import argparse
import atexit
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--start-existing-db', action='store_true',
                        help='Start only the existing verified mindmorph-db container; never create or migrate storage.')
    args = parser.parse_args()
    from tools.isolated_runner import _prefix
    cli = _prefix()
    hold = None
    if Path(cli[0]).stem.lower() == 'wsl':
        hold = subprocess.Popen(cli[:-1] + ['sleep', 'infinity'], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        atexit.register(hold.terminate)
    if os.getenv('MINDMORPH_STORE', 'postgres') == 'postgres':
        if args.start_existing_db:
            image = subprocess.check_output(cli + ['inspect', '--format', '{{.Config.Image}}', 'mindmorph-db'],
                text=True, timeout=30).strip()
            if image != 'pgvector/pgvector:pg16':
                raise RuntimeError('Refusing to start an unrecognized database image')
            subprocess.run(cli + ['start', 'mindmorph-db'], check=True, timeout=60,
                stdout=subprocess.DEVNULL)
            for _ in range(30):
                ready = subprocess.run(cli + ['exec', 'mindmorph-db', 'pg_isready'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if ready.returncode == 0:
                    break
                __import__('time').sleep(.5)
            else:
                raise RuntimeError('The existing database did not become ready')
        from persistence.db import check_ready
        check_ready()
    import uvicorn
    server = uvicorn.Server(uvicorn.Config('api.main:app', host='127.0.0.1', port=args.port, timeout_graceful_shutdown=10))
    stop_file = ROOT / 'logs/p2-8-activation-20261030' / ('stop-' + str(os.getpid()) + '.request')
    started = __import__('time').time()
    done = threading.Event()
    def watch():
        while not done.wait(.25):
            if stop_file.exists() and stop_file.stat().st_mtime >= started:
                server.should_exit = True
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        server.run()
    finally:
        done.set()
        from tools.isolated_runner import _runs, _guard, cancel_run
        with _guard: active = list(_runs)
        for run in active: cancel_run(run)
        if hold: hold.terminate()


if __name__ == '__main__': main()
