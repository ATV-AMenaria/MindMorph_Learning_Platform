import pytest

from evals.bkt_evaluation import demo, evaluate


def test_demo_is_explicitly_simulated_and_uses_real_model():
    result = demo()
    assert result["simulated"]
    learners = result["learners"]
    assert learners["Rahul"]["knowledge_tracking"]["recommendation"]["mode"] == "foundation"
    assert learners["Priya"]["knowledge_tracking"]["recommendation"]["mode"] == "challenge"
    assert result["evaluation"]["evaluated_opportunities"] == 4


def test_next_exercise_metrics_are_chronological_and_do_not_use_current_answer():
    report = evaluate([[True, False]])
    # After one success, latent understanding is 0.5764705882. The forecast
    # before the next (wrong) answer is 0.6035294118, not the updated state.
    assert report["metrics"]["bkt"]["brier_score"] == pytest.approx(0.6035294117647059 ** 2)
    assert report["metrics"]["last_outcome"]["brier_score"] == 1
    assert report["metrics"]["historical_rate"]["brier_score"] == pytest.approx((2 / 3) ** 2)
    assert evaluate([[], [True]])["metrics"]["bkt"]["brier_score"] is None
