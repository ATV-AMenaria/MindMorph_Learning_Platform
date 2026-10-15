"""Tests for Laya primary router and OrchestratorAgent fallback behavior."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from agents.orchestrator.orchestrator_agent import OrchestratorAgent
from agents.orchestrator.orchestrator_agent_output_schema import Orchestrator_Output_Schema
from services.routing.laya_router import is_laya_enabled, route_with_laya


def test_is_laya_enabled_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MINDMORPH_USE_LAYA", None)
        assert is_laya_enabled() is True


def test_is_laya_enabled_disabled():
    with patch.dict(os.environ, {"MINDMORPH_USE_LAYA": "0"}):
        assert is_laya_enabled() is False
    with patch.dict(os.environ, {"MINDMORPH_USE_LAYA": "false"}):
        assert is_laya_enabled() is False


def test_orchestrator_uses_laya_when_available(monkeypatch):
    """When Laya returns a valid route, Orchestrator uses it directly without calling LLM."""
    monkeypatch.setattr("agents.orchestrator.orchestrator_agent.route_with_laya", lambda q: "CONTENT")

    agent = OrchestratorAgent(push_to_langsmith=False)
    mock_llm = MagicMock()
    agent.structured_llm = mock_llm

    result = agent.route_query("Explain how pointers work in C")
    assert isinstance(result, Orchestrator_Output_Schema)
    assert result.Assigned_Agent == "CONTENT"
    assert "Laya local decision model" in result.Reasoning
    mock_llm.invoke.assert_not_called()


def test_orchestrator_falls_back_to_llm_when_laya_unavailable(monkeypatch):
    """When Laya returns None, Orchestrator seamlessly falls back to LLM router."""
    monkeypatch.setattr("agents.orchestrator.orchestrator_agent.route_with_laya", lambda q: None)

    agent = OrchestratorAgent(push_to_langsmith=False)
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = Orchestrator_Output_Schema(
        Assigned_Agent="SCOUT",
        Reasoning="LLM decided user needs a new roadmap."
    )
    agent.structured_llm = mock_llm

    result = agent.route_query("Teach me Docker from scratch")
    assert isinstance(result, Orchestrator_Output_Schema)
    assert result.Assigned_Agent == "SCOUT"
    assert "LLM decided" in result.Reasoning
    mock_llm.invoke.assert_called_once()


def test_orchestrator_falls_back_to_llm_when_laya_raises_exception(monkeypatch):
    """When Laya raises an unexpected exception, Orchestrator catches it and falls back to LLM."""
    def _raise(q):
        raise RuntimeError("Model weight corruption or out of memory")

    monkeypatch.setattr("agents.orchestrator.orchestrator_agent.route_with_laya", _raise)

    agent = OrchestratorAgent(push_to_langsmith=False)
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = Orchestrator_Output_Schema(
        Assigned_Agent="EXERCISE",
        Reasoning="LLM exercise selection."
    )
    agent.structured_llm = mock_llm

    result = agent.route_query("Give me 5 exercises on recursion")
    assert isinstance(result, Orchestrator_Output_Schema)
    assert result.Assigned_Agent == "EXERCISE"
    mock_llm.invoke.assert_called_once()


def test_orchestrator_validates_empty_query():
    agent = OrchestratorAgent(push_to_langsmith=False)
    with pytest.raises(ValueError):
        agent.route_query("")
    with pytest.raises(ValueError):
        agent.route_query("   ")
