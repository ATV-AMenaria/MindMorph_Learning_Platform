# AI Teaching Assistant (architecture §2, P3 #10): a streaming chat tutor grounded in the open lesson
# and the learner's uploaded material. Uses the shared `config.get_chat_model` chain (Groq pool ahead
# of local Ollama) at a warmer temperature, streaming token-by-token via `.astream()`.

import logging
import os
import sys
from typing import Any, AsyncIterator, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from prompts.tutor_prompt import TUTOR_SYSTEM_PROMPT

MAX_LESSON_CHARS = 4000  # cap the grounding context so the prompt stays bounded
MAX_HISTORY_TURNS = 12
TUTOR_TEMPERATURE = 0.3  # warmer than the 0.1 the structured-output agents use


class TutorAgent:
    """Builds grounded chat messages and streams the model's reply token-by-token."""

    def __init__(self, model: Optional[Any] = None):
        self._model = model

    def _get_model(self) -> Any:
        if self._model is None:
            from config import get_chat_model

            # Same provider chain as every other agent (Groq pool ahead of local Ollama), just warmer
            # — this is conversation, not structured extraction. Built lazily so import stays cheap.
            self._model = get_chat_model("default", temperature=TUTOR_TEMPERATURE)
        return self._model

    def build_messages(
        self,
        *,
        skill_label: str,
        lesson_content: Optional[str],
        rag_context: Optional[str],
        history: list[dict],
        question: str,
    ) -> list[BaseMessage]:
        system = TUTOR_SYSTEM_PROMPT.format(
            skill_label=skill_label or "this topic",
            lesson_content=(lesson_content or "(no lesson open yet)")[:MAX_LESSON_CHARS],
            rag_context=rag_context or "(no uploaded material matched)",
        )
        messages: list[BaseMessage] = [SystemMessage(content=system)]
        for turn in (history or [])[-MAX_HISTORY_TURNS:]:
            role, content = turn.get("role"), turn.get("content", "")
            if role == "assistant":
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
        messages.append(HumanMessage(content=question))
        return messages

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[str]:
        """Yield content tokens as they arrive."""
        async for chunk in self._get_model().astream(messages):
            text = getattr(chunk, "content", "") or ""
            if text:
                yield text
