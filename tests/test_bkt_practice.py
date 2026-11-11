"""Recommendations must reach generation without bypassing saved-task recovery."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from persistence.grading_store import GradingStore
from services import knowledge_tracking as tracking
from services.learning_service import new_practice
from services.practice_tasks import replace_task, PracticeUnavailableError
from tests.test_knowledge_tracking import repo, install, submit, exercise


def history(repo):
    for index, success in enumerate([False, False, True]):
        ex = install(repo, identity=f"task-{index}")
        submit(repo, ex, attempt=f"grade-{index}", success=success)
    return ex


def test_service_passes_guidance_and_preserves_lesson_context(repo, monkeypatch):
    ex = history(repo)
    state = repo.get("u", "s")
    state["primary_language"] = "javascript"
    state["lessons"]["a"]["grounding"] = {"status": "disabled", "passages": [], "sources": {}}
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"exercise_statement": "New task", "grading_artifact": {"checks": ["test"]},
        "exercise_format": "coding_challenge", "exercise_language": "javascript"})
    monkeypatch.setattr("graph.exercise_graph.build_exercise_graph", lambda: graph)
    hint = {"mode": "foundation", "reason": "Practice foundations"}
    result = new_practice(state, "a", "u", practice_guidance=hint)
    sent = graph.ainvoke.call_args.args[0]
    assert sent["practice_guidance"] == hint
    assert sent["primary_language"] == "javascript" and sent["lesson_content"] == "# Multiplication"
    assert sent["grounding"] == state["lessons"]["a"]["grounding"]
    assert result["practice_mode"] == "foundation" and result["id"] != ex["id"]
    monkeypatch.setenv("MINDMORPH_BKT", "0")
    result = new_practice(state, "a", "u", practice_guidance=hint)
    assert "practice_mode" not in result and graph.ainvoke.call_args.args[0]["practice_guidance"] is None


async def test_guidance_reaches_synthesizer_and_does_not_change_grading_contract(repo):
    from graph.exercise_graph import build_exercise_graph
    selector, synth, grader = MagicMock(), MagicMock(), MagicMock()
    selector.select_format.return_value = MagicMock(format="coding_challenge", language="python", reasoning="Practice")
    synth.synthesize.return_value = "A smaller problem"
    grader.build_grading_artifact.return_value.model_dump.return_value = {"instructions": "Submit code", "unit_tests": ["test"]}
    graph = build_exercise_graph(format_selector=selector, synthesizer=synth, grader=grader,
        github=AsyncMock(return_value=None), blog=MagicMock(), dataset=MagicMock())
    out = await graph.ainvoke({"user_query": "Loops", "practice_guidance": {"mode": "foundation", "reason": "History"}})
    prompt = synth.synthesize.call_args.args[0]
    assert "smaller problem" in prompt and "worked example" in prompt and "Loops" in prompt
    assert "locked prerequisites" not in prompt  # challenge-only guidance
    assert grader.build_grading_artifact.call_args.args[2] == "A smaller problem"
    assert out["grading_artifact"]["unit_tests"] == ["test"]
    challenge = tracking.guidance_prompt({"mode": "challenge", "reason": "History"})
    assert "locked prerequisites" in challenge and "current skill" in challenge


def test_generation_retry_uses_guidance_frozen_at_acceptance(repo, monkeypatch):
    ex = history(repo)
    hints = []
    def generate(*args, practice_guidance=None):
        hints.append(practice_guidance)
        if len(hints) == 1:
            raise RuntimeError("Temporary generation outage")
        return exercise("new")
    monkeypatch.setattr("services.learning_service.new_practice", generate)
    with pytest.raises(RuntimeError):
        replace_task(repo, "u", "s", "a", ex["revision"], "frozen")
    assert hints[0]["mode"] == "foundation"
    original = tracking.summarize
    monkeypatch.setattr(tracking, "summarize", lambda rows: {**original(rows),
        "recommendation": {"mode": "challenge", "reason": "A later estimate"}})
    result = replace_task(repo, "u", "s", "a", ex["revision"], "frozen")
    assert hints[0] == hints[1] and hints[1]["mode"] == "foundation"
    assert result["lessons"]["a"]["content"] == "# Multiplication"
    replace_task(repo, "u", "s", "a", ex["revision"], "frozen")
    assert len(hints) == 2
    assert len(GradingStore(repo).knowledge_observations("u", "s", "a")) == 3


def test_failed_evaluator_release_preserves_saved_exercise_and_draft_revision(repo, monkeypatch):
    ex = history(repo)
    monkeypatch.setattr("services.learning_service.new_practice", lambda *args, **kwargs:
        {**exercise("unverified"), "grading_artifact": {"unit_tests": ["pass"]}})
    for _ in range(2):
        with pytest.raises(PracticeUnavailableError):
            replace_task(repo, "u", "s", "a", ex["revision"], "bad")
    assert repo.get("u", "s")["lessons"]["a"]["exercise"]["revision"] == ex["revision"]


def test_practice_request_rebuilds_estimate_after_dependency_outage(repo, monkeypatch):
    real = tracking.predict_history
    monkeypatch.setattr(tracking, "predict_history", lambda *args: (_ for _ in ()).throw(ImportError()))
    ex = history(repo)
    assert repo.get("u", "s")["node_state"]["a"]["knowledge_tracking"]["status"] == "unavailable"
    monkeypatch.setattr(tracking, "predict_history", real)
    seen = []
    def generate(*args, **kwargs):
        seen.append(kwargs["practice_guidance"])
        return exercise("recovered")
    monkeypatch.setattr("services.learning_service.new_practice", generate)
    result = replace_task(repo, "u", "s", "a", ex["revision"], "recover")
    assert seen[0]["mode"] == "foundation"
    assert result["node_state"]["a"]["knowledge_tracking"]["status"] == "ready"
