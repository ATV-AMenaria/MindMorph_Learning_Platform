"""Server-owned runtime manifest. Images become usable only after worker probes."""
from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class RuntimeProfile:
    id: str
    revision: str
    entrypoint: str
    packages: tuple[str, ...] = ()
    memory_mb: int = 512
    cpus: int = 1
    run_seconds: int = 10


PROFILES = {
    p.id: p for p in (
        RuntimeProfile("python-core", "1", "main.py", ("Python==3.11.18",)),
        RuntimeProfile("python-data", "1", "main.py", ("Python==3.11.18", "numpy==2.2.2", "pandas==2.2.3", "scipy==1.15.1", "scikit-learn==1.6.1"), 2048, 2, 30),
        RuntimeProfile("javascript-node", "1", "main.js", ("Node==22.23.4",)),
        RuntimeProfile("typescript-node", "2", "main.ts", ("Node==22.23.4", "typescript==5.7.3", "@types/node==22.10.2")),
        RuntimeProfile("sqlite", "1", "main.sql", ("Python==3.11.18", "SQLite (image-pinned)")),
        RuntimeProfile("react-dom", "2", "App.tsx", ("react==19.0.0", "react-dom==19.0.0", "typescript==5.7.3", "playwright-core==1.60.0", "esbuild==0.25.0", "Chromium (image-pinned)"), 2048, 2, 30),
        *(RuntimeProfile(name, "1", entry, (), 2048, 2, 30) for name, entry in (
            ("postgresql", "main.sql"), ("c", "main.c"), ("cpp", "main.cpp"),
            ("java", "Main.java"), ("csharp", "Program.cs"), ("go", "main.go"),
            ("rust", "main.rs"), ("r", "main.R"))),
    )
}


def manifest_hash(profile_id):
    data = asdict(PROFILES[profile_id])
    data["adapter_version"] = 3
    if profile_id == "react-dom":
        from pathlib import Path
        worker = Path(__file__).resolve().parents[1] / "worker"
        data["browser_template"] = {name: hashlib.sha256((worker / name).read_bytes()).hexdigest()
            for name in ("browser_build.cjs", "browser_run.cjs", "browser-seccomp.json")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def commands(profile_id, entry):
    """Trusted argv only; submitted entry paths have already been validated."""
    from pathlib import PurePosixPath
    compile_commands = {
        "react-dom": ["node", "/opt/mm/browser_build.cjs", entry],
        "typescript-node": ["tsc", "--outDir", "out", "--target", "ES2022", "--module", "commonjs", "--typeRoots", "/opt/mm/node_modules/@types", entry],
        "c": ["gcc", "-O0", entry, "-o", "/work/program"],
        "cpp": ["g++", "-O0", entry, "-o", "/work/program"],
        "java": ["javac", "-J-XX:MaxRAMPercentage=60", "-d", "out", entry],
        "csharp": ["mcs", "-out:/work/program.exe", entry],
        "go": ["go", "build", "-p", "2", "-o", "/work/program", entry],
        "rust": ["rustc", entry, "-o", "/work/program"],
    }
    run_commands = {
        "python-core": ["python", "-I", "-c", "import runpy,sys; sys.path.insert(0,'/work'); runpy.run_path(sys.argv[1],run_name='__main__')", entry],
        "javascript-node": ["node", entry],
        "react-dom": ["node", "/opt/mm/browser_run.cjs"],
        "typescript-node": ["node", "out/" + str(PurePosixPath(entry).with_suffix(".js"))],
        "sqlite": ["python", "-I", "/opt/mm/sqlite_main.py", entry],
        "c": ["/work/program"], "cpp": ["/work/program"],
        "java": ["java", "-XX:MaxRAMPercentage=60", "-XX:ActiveProcessorCount=2", "-cp", "out", PurePosixPath(entry).stem],
        "csharp": ["mono", "/work/program.exe"], "go": ["/work/program"], "rust": ["/work/program"],
        "r": ["Rscript", "--vanilla", entry],
        "postgresql": ["psql", "--no-psqlrc", "--quiet", "--host", "/work", "--username", "worker", "--dbname", "postgres", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", "--file", entry],
    }
    run_commands["python-data"] = run_commands["python-core"]
    return compile_commands.get(profile_id), run_commands[profile_id]


def execution_contract(profile_id):
    if profile_id == "react-dom":
        return ("Submit App.tsx exporting a default React component, with explicit React imports and no required props. "
            "The trusted template typechecks TSX, mounts the component in a real sandboxed Chromium browser, and reads "
            "a JSON object from stdin: actions (up to12 click/fill/check/press/wait) and queries (up to20). "
            "Targets use selector or accessible role/name; fill takes value, press takes key, wait takes ms<=1000. "
            "Queries read text/value/visible/count/attribute/aria; attribute queries take attribute. "
            "Actions and queries are FLAT objects: put selector or role/name directly on each item, never inside target. "
            "Actions use type; queries use read (not type). Valid example: "
            '{"actions":[{"type":"click","role":"button","name":"Increment"}],'
            '"queries":[{"selector":"output","read":"text"},{"role":"button","name":"Increment","read":"count"}]}. '
            'For aria-live use {"selector":"output","read":"attribute","attribute":"aria-live"}; '
            "read=aria returns an accessibility snapshot, not one ARIA attribute. "
            "Output is a JSON array of query observations in order. Optional mocks maps /api/path to local JSON. "
            "All external network is blocked. Do not write a Node program, a browser driver, a test suite, or HTML scaffolding.")
    if profile_id == "sqlite":
        return "Submit a complete SQLite SQL script; each result query emits its rows as a JSON array. No external database or network."
    if profile_id == "postgresql":
        return "Submit a PostgreSQL script for an empty disposable database; psql prints result rows with | separators. No SQLite dialect substitution."
    return "Submit a complete program reading stdin and writing stdout; use only advertised installed packages and declared local fixtures."
