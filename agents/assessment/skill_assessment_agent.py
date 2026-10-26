# Skill Assessment (architecture §2): generates a diagnostic MCQ quiz from the skill graph so the
# learner can test out of skills they already know (P2 #8). Structured output, graceful None.

import logging
import os
import sys
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)

from config import llm
from agents.assessment.assessment_schema import AssessmentQuiz
from prompts.assessment_prompt import ASSESSMENT_HUMAN_TEMPLATE, ASSESSMENT_SYSTEM_PROMPT


class SkillAssessmentAgent:
    """Generates a diagnostic MCQ quiz for a skill graph (structured output)."""

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = llm
        self.structured_llm = self.llm.with_structured_output(AssessmentQuiz, method="json_schema")
        system_template = SystemMessagePromptTemplate.from_template(ASSESSMENT_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(ASSESSMENT_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def assess(self, skill_graph_json: str) -> Optional[AssessmentQuiz]:
        """Return an AssessmentQuiz, or None on failure / empty input."""
        if not skill_graph_json or not isinstance(skill_graph_json, str):
            raise ValueError("skill_graph_json must be a non-empty string")

        logger.info("Assessment: generating diagnostic quiz")
        try:
            result = self.structured_llm.invoke(
                self.chat_prompt.format_messages(skill_graph_json=skill_graph_json)
            )
            if result is not None:
                logger.info("Assessment: generated %d question(s)", len(result.questions))
            return result
        except Exception:
            logger.exception("Assessment: error generating quiz")
            return None
