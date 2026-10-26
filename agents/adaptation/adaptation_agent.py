# Phase 3 — Adapts the Skill Dependency Graph from a graded node's score + feedback.
#
# Mirrors agents/reviewer/reviewer_agent.py: a structured-output LLM call that returns a typed
# result (GraphAdaptation) or None on failure. The agent only PROPOSES the change (additive nodes
# /edges + remediation focus); the deterministic merge in graph/skill_graph_adapt.py applies it and
# enforces the id-stability invariant.

import logging
import sys
import os
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)

from config import get_chat_model
from prompts.adaptation_prompt import ADAPTATION_SYSTEM_PROMPT, ADAPTATION_HUMAN_TEMPLATE
from agents.adaptation.adaptation_schema import GraphAdaptation


class AdaptationAgent:
    """Proposes an additive skill-graph adaptation after a node is graded."""

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("default")  # fires on the interactive post-grade remediation beat
        self.structured_llm = self.llm.with_structured_output(GraphAdaptation, method="json_schema")
        system_template = SystemMessagePromptTemplate.from_template(ADAPTATION_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(ADAPTATION_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def adapt(
        self,
        skill_graph_json: str,
        node_id: str,
        score: float,
        feedback: str,
    ) -> Optional[GraphAdaptation]:
        """Return a proposed GraphAdaptation, or None on failure.

        skill_graph_json / feedback are passed as prompt VARIABLES (not f-string-injected), so any
        literal `{`/`}` in the JSON or feedback is substituted verbatim and never parsed as a
        template placeholder.
        """
        if not node_id or not isinstance(node_id, str):
            raise ValueError("node_id must be a non-empty string")

        logger.info("Adaptation: evaluating node %r (score=%s)", node_id, score)
        try:
            messages = self.chat_prompt.format_messages(
                skill_graph_json=skill_graph_json,
                node_id=node_id,
                score=score,
                feedback=feedback or "(no feedback)",
            )
            result = self.structured_llm.invoke(messages)
            if result is not None:
                logger.info(
                    "Adaptation: +%d node(s) +%d edge(s) focus=%s",
                    len(result.new_nodes), len(result.new_edges), result.remediation_focus,
                )
            return result
        except Exception:
            logger.exception("Adaptation: error adapting skill graph for node %r", node_id)
            return None


if __name__ == "__main__":
    agent = AdaptationAgent()
    a = agent.adapt(
        '{"summary":"x","nodes":[{"id":"loops","label":"Loops","description":"...","level":"foundational"}],"edges":[]}',
        "loops",
        20.0,
        "Off-by-one errors; did not handle empty input.",
    )
    print(a)
