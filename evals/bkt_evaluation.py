"""Chronological next-exercise evaluation; no fitting or improvement claims.

    python -m evals.bkt_evaluation --demo
    python -m evals.bkt_evaluation --user-id USER --session-id SESSION --node-id NODE
"""
import argparse
import json
import math
from statistics import mean

from services import knowledge_tracking as tracking


def evaluate(histories: list[list[bool]]) -> dict:
    errors = {"bkt": [], "last_outcome": [], "historical_rate": []}
    losses = {key: [] for key in errors}
    for outcomes in histories:
        states = tracking.predict_history(outcomes)
        successes = 0
        for index, actual in enumerate(outcomes):
            # Evaluate only opportunities with prior evidence. All forecasts are
            # computed before seeing the answer being scored; no model is fitted.
            if index:
                p = states[index - 1]
                forecasts = {"bkt": p * (1 - tracking.SLIP) + (1 - p) * tracking.GUESS,
                             "last_outcome": float(outcomes[index - 1]),
                             "historical_rate": (successes + 1) / (index + 2)}
                for name, forecast in forecasts.items():
                    errors[name].append((forecast - int(actual)) ** 2)
                    bounded = max(1e-9, min(1 - 1e-9, forecast))
                    losses[name].append(-math.log(bounded if actual else 1 - bounded))
            successes += actual
    return {"model_version": tracking.MODEL_VERSION, "evaluated_opportunities": len(errors["bkt"]),
            "metrics": {name: {"brier_score": mean(values) if values else None,
                               "log_loss": mean(losses[name]) if values else None}
                        for name, values in errors.items()},
            "interpretation": "Lower is better. Pilot parameters are uncalibrated; this report does not establish improved learning outcomes."}


def demo() -> dict:
    histories = {"Rahul": [False, False, True], "Priya": [True, True, True]}
    learners = {}
    for name, outcomes in histories.items():
        rows = [{"successful": value} for value in outcomes]
        summary = tracking.summarize(rows)
        learners[name] = {"exercise_scores": [100 if value else 50 for value in outcomes],
                          "existing_mastery": "mastered", "knowledge_tracking": summary}
    return {"simulated": True, "learners": learners, "evaluation": evaluate(list(histories.values()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--user-id")
    parser.add_argument("--session-id")
    parser.add_argument("--node-id")
    args = parser.parse_args()
    if args.demo:
        if any((args.user_id, args.session_id, args.node_id)):
            parser.error("Use either --demo or a complete learner/session/node scope")
        report = demo()
    else:
        if not all((args.user_id, args.session_id, args.node_id)):
            parser.error("Supply --demo or all of --user-id, --session-id and --node-id")
        from persistence.grading_store import GradingStore
        from persistence.repository import get_default_repository
        rows = GradingStore(get_default_repository()).knowledge_observations(args.user_id, args.session_id, args.node_id)
        report = {"simulated": False, "observation_count": len(rows),
                  "evaluation": evaluate([[row["successful"] for row in rows]])}
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
