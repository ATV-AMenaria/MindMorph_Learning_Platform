# Master LLM: merges the creative draft (Path A) with factual web findings (Path B).

import logging
import sys
import os
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)

from config import get_chat_model
from prompts.synthesizer_prompt import SYNTHESIZER_SYSTEM_PROMPT

_HUMAN_TEMPLATE = """Topic: {user_query}

CREATIVE DRAFT:
{creative_draft}

FACTUAL FINDINGS:
{factual_findings}

Produce the final merged lesson."""


class SynthesizerAgent:
    '''Merges a creative draft with factual findings into the final lesson.'''

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("default")  # interactive lesson-generation path
        system_template = SystemMessagePromptTemplate.from_template(SYNTHESIZER_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def synthesize(
        self,
        user_query: str,
        creative_draft: str,
        factual_findings: Optional[str],
    ) -> str:
        '''Returns the final lesson. Falls back to the creative draft if no factual findings.'''
        if not creative_draft:
            raise ValueError("creative_draft is required")

        # Degrade gracefully: with no live grounding, the creative draft is the lesson.
        if not factual_findings:
            return creative_draft

        logger.info("Synthesizer: merging creative draft with factual findings")
        try:
            messages = self.chat_prompt.format_messages(
                user_query=user_query,
                creative_draft=creative_draft,
                factual_findings=factual_findings,
            )
            return self.llm.invoke(messages).content
        except Exception:
            logger.exception("Synthesizer: error merging content; falling back to creative draft")
            # On failure, the creative draft is still a usable lesson.
            return creative_draft


if __name__ == "__main__":
    agent = SynthesizerAgent()
    print(agent.synthesize("Python lists", "Lists are ordered collections...", "- Python 3.13 released (Source: x)"))
