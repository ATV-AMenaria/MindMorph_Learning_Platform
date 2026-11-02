"""Independent compiler/dialect observations; enables only profiles which pass."""
import json
import os
from pathlib import Path

from tools import isolated_runner as runner
from tools.runtime_profiles import PROFILES, manifest_hash
from worker.inventory import collect


def main():
    out = Path("logs/p2-8-implementation")
    build = json.loads((out / "worker-tools-build.json").read_text())
    programs = {
        "c": '#include <stdio.h>\nint main(){int n;scanf("%d",&n);printf("%d\\n",n*2);return 0;}',
        "cpp": '#include <iostream>\nint main(){int n;std::cin>>n;std::cout<<n*2<<std::endl;}',
        "java": 'import java.util.*;public class Main{public static void main(String[] a){System.out.println(new Scanner(System.in).nextInt()*2);}}',
        "csharp": 'using System;class Program{static void Main(){Console.WriteLine(int.Parse(Console.ReadLine())*2);}}',
        "go": 'package main\nimport "fmt"\nfunc main(){var n int;fmt.Scan(&n);fmt.Println(n*2)}',
        "rust": 'use std::io::{self,Read};fn main(){let mut s=String::new();io::stdin().read_to_string(&mut s).unwrap();let n:i32=s.trim().parse().unwrap();println!("{}",n*2);}',
        "r": 'n <- as.numeric(readLines(file("stdin"),n=1)); cat(n*2,"\\n",sep="")',
        "postgresql": 'CREATE TABLE numbers(n int); INSERT INTO numbers VALUES(21); SELECT n*2 FROM numbers;',
    }
    candidate = {"probe_version": 1, "profiles": {key: {"image": build["image"], "revision": PROFILES[key].revision,
        "manifest_hash": manifest_hash(key), "actual_versions": {"candidate": "operator probe"}, "probes_passed": True}
        for key in programs}}
    candidate_path = out / "tools-candidate.json"
    candidate_path.write_text(json.dumps(candidate))
    os.environ["MM_WORKER_LOCK"] = str(candidate_path)
    records = []
    enabled = {}
    for profile, code in programs.items():
        entry = PROFILES[profile].entrypoint
        samples = [("correct", code, "21\n", "42\n"), ("alternate", code + "\n", "0\n", "0\n"),
                   ("wrong", "", "21\n", None), ("syntax", "not valid source !!!", "21\n", None)]
        if profile == "postgresql":
            samples[1] = ("alternate", "SELECT 6*7;", "", "42\n")
        passed = True
        for name, source, stdin, expected in samples:
            result = runner.execute_profile(profile, {entry: source}, stdin=stdin)
            good = (result["status"] == "succeeded" and result["stdout"] == expected) if expected is not None else (
                result["status"] in {"succeeded", "learner_error"} and result["stdout"] != "42\n")
            records.append({"profile": profile, "case": name, "passed": good, "result": result})
            passed &= good
            print(profile, name, good, result["status"], flush=True)
        if passed:
            enabled[profile] = candidate["profiles"][profile]
    inventory = collect(runner._prefix(), build["image"])
    for row in enabled.values():
        row["actual_versions"] = {"python": inventory["python"], **inventory["tools"]}
    lock = json.loads(Path("worker/profiles.lock.json").read_text())
    lock["profiles"].update(enabled)
    (out / "worker-tool-probes.json").write_text(json.dumps(records, indent=2))
    Path("worker/profiles.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    if len(enabled) != len(programs):
        raise SystemExit("Some compiler profiles failed and remain unavailable")


if __name__ == "__main__":
    main()
