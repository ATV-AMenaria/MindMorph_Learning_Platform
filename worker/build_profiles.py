"""Explicit operator build. Never called by request handling; no lock enablement."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def main():
    from tools.isolated_runner import _prefix
    cli = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].startswith("[") else _prefix()
    evidence = Path("logs/p2-8-implementation")
    evidence.mkdir(parents=True, exist_ok=True)
    images = {}
    for key, tag in (("PYTHON_IMAGE", "python:3.11.18-slim-bookworm"), ("NODE_IMAGE", "node:22.23.4-bookworm-slim")):
        subprocess.run(cli + ["pull", tag], check=True)
        digest = subprocess.check_output(cli + ["image", "inspect", "--format", "{{index .RepoDigests 0}}", tag], text=True).strip()
        images[key] = digest
    context = io.BytesIO()
    is_tools = "--tools" in sys.argv
    is_browser = "--browser" in sys.argv
    with tarfile.open(fileobj=context, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name in ("Dockerfile.browser" if is_browser else "Dockerfile.tools" if is_tools else "Dockerfile", "worker_main.py", "sqlite_main.py", "start_postgres.py", "browser_build.cjs", "browser_run.cjs"):
            archive.add(Path(__file__).parent / name, arcname="Dockerfile" if name.startswith("Dockerfile") else name)
    tag = "mm-grading-browser:1" if is_browser else "mm-grading-tools:1" if is_tools else "mm-grading-core:1"
    args = cli + ["build", "--tag", tag]
    if is_tools or is_browser:
        core = json.loads((evidence / "worker-build.json").read_text())["image"]
        # Dockerfile FROM needs a named local reference, not a bare image ID.
        core_tag = "mm-grading-core:checkpoint-" + core.removeprefix("sha256:")[:16]
        subprocess.run(cli + ["tag", core, core_tag], check=True)
        args += ["--build-arg", "CORE_IMAGE=" + core_tag]
        images["CORE_IMAGE"] = core
    for key, digest in images.items():
        if key != "CORE_IMAGE":
            args += ["--build-arg", key + "=" + digest]
    subprocess.run(args + ["-"], input=context.getvalue(), check=True)
    image = subprocess.check_output(cli + ["image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
    record = {"image": image, "base_images": images}
    (evidence / ("worker-browser-build.json" if is_browser else "worker-tools-build.json" if is_tools else "worker-build.json")).write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


if __name__ == "__main__":
    main()
