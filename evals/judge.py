# Content-groundedness LLM-as-judge (eval pipeline). Mirrors the reviewer agent: structured output,
# graceful None, no side effects on import. Judges PRECISION (contradiction of source facts), not coverage.

import logging
import os
import sys
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from pydantic import BaseModel, Field

from prompts.eval_groundedness_prompt import (
    GROUNDEDNESS_HUMAN_TEMPLATE,
    GROUNDEDNESS_SYSTEM_PROMPT,
)


class GroundednessScore(BaseModel):
    score: float = Field(description="0-100; 100 = no claim contradicts the source facts.")
    grounded: bool = Field(description="True if free of contradictions/hallucinations (roughly score>=70).")
    feedback: str = Field(description="Specific contradicted/false claims, or a note that it is clean.")


class GroundednessJudge:
    """LLM-as-judge scoring how well generated content is grounded in curated source facts."""

    def __init__(self, model=None):
        from config import llm

        self.llm = model or llm
        self.structured_llm = self.llm.with_structured_output(GroundednessScore, method="json_schema")
        self.chat_prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(GROUNDEDNESS_SYSTEM_PROMPT),
            HumanMessagePromptTemplate.from_template(GROUNDEDNESS_HUMAN_TEMPLATE),
        ])

    def judge(self, query: str, content: str, source_facts: str) -> Optional[GroundednessScore]:
        try:
            return self.structured_llm.invoke(
                self.chat_prompt.format_messages(
                    query=query, content=content, source_facts=source_facts
                )
            )
        except Exception:
            logger.exception("GroundednessJudge: scoring failed")
            return None
