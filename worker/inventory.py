"""Collect exact runtime, transitive package and license metadata from an image."""
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = r'''
import importlib.metadata as m,json,platform,sqlite3,subprocess
from pathlib import Path
def version(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=10)
  return (p.stdout or p.stderr).strip().splitlines()[0]
 except (OSError,IndexError): return None
packages={d.metadata['Name']:{'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License') or 'See distribution license files'} for d in m.distributions()}
node=json.loads(Path('/opt/mm/package-lock.json').read_text())
tools={name:version(args) for name,args in {'node':['node','--version'],'typescript':['tsc','--version'],'chromium':['chromium','--version'],'gcc':['gcc','--version'],'g++':['g++','--version'],'java':['java','-version'],'mono':['mono','--version'],'go':['go','version'],'rustc':['rustc','--version'],'R':['R','--version'],'postgresql':['psql','--version']}.items()}
print(json.dumps({'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'tools':tools,'python_packages':packages,'npm_lock':node,'os_packages':subprocess.check_output(['dpkg-query','-W','-f=${Package}=${Version}\n'],text=True),'os_licenses':'Image /usr/share/doc/<package>/copyright; runtime image retains license files'}))
'''


def collect(cli, image):
    result = subprocess.check_output(cli + ["run", "--rm", "--name", "mm-grading-inventory-" + image[-12:], "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "512m", "--pids-limit", "32", "--entrypoint", "python", image, "-I", "-c", SCRIPT], text=True, timeout=60)
    return json.loads(result)


def main():
    from tools.isolated_runner import _prefix
    cli = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].startswith("[") else _prefix()
    kind = "browser" if "--browser" in sys.argv else "tools" if "--tools" in sys.argv else "core"
    build_path = Path("logs/p2-8-implementation") / ("worker-" + kind + "-build.json" if kind != "core" else "worker-build.json")
    build = json.loads(build_path.read_text())
    inventory = collect(cli, build["image"])
    out = Path("worker/dependencies.lock.json")
    existing = json.loads(out.read_text()) if out.exists() else {}
    existing[kind] = {"image": build["image"], "inventory": inventory}
    out.write_text(json.dumps(existing, indent=2) + "\n")
    print(kind, build["image"], inventory["python"], inventory["tools"])


if __name__ == "__main__":
    main()
