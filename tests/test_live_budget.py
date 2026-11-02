from types import SimpleNamespace

import pytest

from evals.live_budget import LiveBudget, EvaluationStopped


class Model:
    __module__ = "langchain_groq.chat_models"
    model_name = "openai/gpt-oss-120b"


def test_budget_reserves_before_dispatch_and_survives_process_reload(tmp_path):
    path = tmp_path / "ledger.json"
    budget = LiveBudget(path, calls=1)
    kwargs = {}
    index = budget.reserve(Model(), ["small prompt"], kwargs)
    assert kwargs["max_tokens"] == 8192 and kwargs["timeout"] == 60
    assert budget.totals()["calls"] == 1
    resumed = LiveBudget(path, calls=1)
    with pytest.raises(EvaluationStopped): resumed.reserve(Model(), ["retry"], {})
    assert resumed.totals()["calls"] == 1


def test_observed_usage_releases_unused_reservation_and_error_keeps_upper_bound(tmp_path):
    budget = LiveBudget(tmp_path / "ledger.json")
    index = budget.reserve(Model(), ["small prompt"], {})
    bound = budget.totals()["tokens_upper"]
    result = SimpleNamespace(generations=[SimpleNamespace(message=SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 20}))])
    budget.complete(index, result)
    assert budget.totals()["tokens_upper"] == 120 < bound
    assert budget.totals()["observed_est_usd"] == pytest.approx(.000027)
    failed = budget.reserve(Model(), ["second"], {})
    before = budget.totals()["tokens_upper"]
    budget.complete(failed, error=OSError())
    assert budget.totals()["tokens_upper"] == before


def test_limits_and_throttling_stop_without_an_extra_request(tmp_path):
    for settings in ({"tokens": 1}, {"dollars": .000001}):
        budget = LiveBudget(tmp_path / (next(iter(settings)) + ".json"), **settings)
        with pytest.raises(EvaluationStopped): budget.reserve(Model(), ["prompt"], {})
        assert budget.totals()["calls"] == 0
    budget = LiveBudget(tmp_path / "throttled.json")
    class Throttled(Exception): status_code = 429
    for _ in range(3): budget.complete(budget.reserve(Model(), ["retry"], {}), error=Throttled())
    with pytest.raises(EvaluationStopped): budget.reserve(Model(), ["fourth"], {})
    assert budget.totals()["calls"] == 3
