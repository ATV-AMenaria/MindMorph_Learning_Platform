"""CLI: content-groundedness eval pipeline.

    python -m evals.run [--threshold 70]      # generate lessons → judge → scored report (exit 0/1)
    python -m evals.run --calibrate           # run fixed calibration cases through the REAL judge

Needs a running local Ollama daemon (see config.py) — no API key required. Exit codes: 0 pass / 1 below
threshold / 2 misconfigured.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from evals.runner import run_evals

HERE = Path(__file__).parent
logger = logging.getLogger(__name__)


def _load(name: str) -> list[dict]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def _calibrate() -> int:
    """Validate the judge discriminates: contradiction → low, extra-but-correct → high."""
    from evals.judge import GroundednessJudge

    judge = GroundednessJudge()
    ok = True
    for case in _load("calibration.json"):
        res = judge.judge(case["query"], case["content"], case["source_facts"])
        got = res.grounded if res else None
        want = case["expect_grounded"]
        passed = got == want
        ok = ok and passed
        score = f"{res.score:.0f}" if res else "n/a"
        print(f"[{'OK' if passed else 'XX'}] {case['id']:<36} grounded={got} (want {want}) score={score}")
    print("-" * 50)
    print("calibration:", "PASS — judge discriminates" if ok else "FAIL — judge does NOT discriminate")
    return 0 if ok else 1


def _run(threshold: float) -> int:
    from agents.content_generator.content_agent import ContentAgent
    from evals.judge import GroundednessJudge

    content_agent = ContentAgent(push_to_langsmith=False)
    judge = GroundednessJudge()

    def generate(case: dict) -> str:
        return content_agent.generate_content(case["query"], case.get("format_type", "A"))

    def judge_fn(case: dict, content: str):
        return judge.judge(case["query"], content, case["source_facts"])

    report = run_evals(_load("dataset.json"), generate, judge_fn, threshold=threshold)
    print(report.render())
    return 0 if report.passed else 1


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="MindMorph content-groundedness eval")
    parser.add_argument("--threshold", type=float, default=70.0)
    parser.add_argument("--calibrate", action="store_true", help="validate the judge, don't generate")
    args = parser.parse_args()

    try:
        import httpx

        from config import OLLAMA_HOST

        httpx.get(OLLAMA_HOST, timeout=3)
    except Exception as e:
        print(f"Ollama not reachable at {OLLAMA_HOST!r} ({e}) — this eval needs a live LLM.", file=sys.stderr)
        return 2

    return _calibrate() if args.calibrate else _run(args.threshold)


if __name__ == "__main__":
    sys.exit(main())
