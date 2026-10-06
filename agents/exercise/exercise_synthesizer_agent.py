# Exercise Synthesizer (architecture §6.4): merges raw web material (GitHub repos, blog tutorials,
# dataset links) into one personalized, original practice exercise for the learner's goal.

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
from prompts.exercise_synthesizer_prompt import EXERCISE_SYNTHESIZER_SYSTEM_PROMPT

_HUMAN_TEMPLATE = """Learner goal: {user_query}
Exercise format: {exercise_format}
Coding language: {exercise_language}

GITHUB MATERIAL:
{github_material}

BLOG MATERIAL:
{blog_material}

DATASET MATERIAL:
{dataset_material}

Design the practice exercise."""

_NONE = "(none found)"


class ExerciseSynthesizerAgent:
    """Personalizes gathered material into a single practice exercise statement."""

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("default")  # interactive exercise-generation path
        system_template = SystemMessagePromptTemplate.from_template(EXERCISE_SYNTHESIZER_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def synthesize(
        self,
        user_query: str,
        exercise_format: str,
        exercise_language: Optional[str] = None,
        github_material: Optional[str] = None,
        blog_material: Optional[str] = None,
        dataset_material: Optional[str] = None,
    ) -> Optional[str]:
        """Returns the exercise statement (Markdown), or None on failure.

        Any missing source degrades to "(none found)"; the LLM still produces an exercise from its
        own knowledge plus whatever material is available.
        """
        if not user_query or not isinstance(user_query, str):
            raise ValueError("User query must be a non-empty string")

        logger.info(
            "ExerciseSynthesizer: composing %s exercise for %r", exercise_format, user_query
        )
        try:
            messages = self.chat_prompt.format_messages(
                user_query=user_query,
                exercise_format=exercise_format,
                exercise_language=exercise_language or "(not applicable)",
                github_material=github_material or _NONE,
                blog_material=blog_material or _NONE,
                dataset_material=dataset_material or _NONE,
            )
            return self.llm.invoke(messages).content
        except Exception:
            logger.exception("ExerciseSynthesizer: error composing exercise")
            return None


if __name__ == "__main__":
    agent = ExerciseSynthesizerAgent()
    print(agent.synthesize("recursion in Python", "coding_challenge"))
