# AI Teaching Assistant (architecture §2, P3 #10): a streaming chat tutor grounded in the open lesson
# and the learner's uploaded material. Streams through the central Model Router (ProviderChain) via
# `config.get_chat_model`, which rotates Groq keys on failure, falls back to local Ollama, and
# respects MINDMORPH_TUTOR_PROVIDER / MINDMORPH_TUTOR_FALLBACK / MINDMORPH_TUTOR_TIER env vars so the
# tutor's AI brain can be swapped or upgraded independently of every other agent — no code change
# required. This closes the P3 #10 deferred item: "streaming through the router".

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

# --- Router config (env-var overrides, all optional) ------------------------------------------
# These let you point the tutor at a different model/vendor without touching any code:
#
#   MINDMORPH_TUTOR_PROVIDER  — which AI vendor to use for the tutor (e.g. "groq", "ollama").
#                               Defaults to whatever MINDMORPH_LLM_PROVIDER is set to globally.
#   MINDMORPH_TUTOR_FALLBACK  — fallback vendor if the primary fails (e.g. "ollama", "none").
#                               Defaults to MINDMORPH_LLM_FALLBACK.
#   MINDMORPH_TUTOR_TIER      — model size tier: "default" (fast) or "complex" (more capable).
#                               Defaults to "default".
#
# Examples:
#   MINDMORPH_TUTOR_PROVIDER=groq MINDMORPH_TUTOR_TIER=complex  → tutor uses the larger Groq model
#   MINDMORPH_TUTOR_PROVIDER=ollama                             → tutor always uses local Ollama
_TUTOR_PROVIDER = os.getenv("MINDMORPH_TUTOR_PROVIDER")   # None → inherits global default
_TUTOR_FALLBACK = os.getenv("MINDMORPH_TUTOR_FALLBACK")   # None → inherits global default
_TUTOR_TIER     = os.getenv("MINDMORPH_TUTOR_TIER", "default")


class TutorAgent:
    """Builds grounded chat messages and streams the model's reply token-by-token.

    The model is resolved lazily through ``config.get_chat_model`` so that:
    - No network call happens at import time.
    - The provider / fallback / tier can be overridden per-instance (for tests) or via env vars
      (MINDMORPH_TUTOR_PROVIDER, MINDMORPH_TUTOR_FALLBACK, MINDMORPH_TUTOR_TIER) for production.
    - The returned object is always a ProviderChain (for Groq) or a bare ChatOllama — both expose
      the same ``.astream()`` surface, so the streaming loop below is identical for both.
    """

    def __init__(
        self,
        model: Optional[Any] = None,
        *,
        provider: Optional[str] = None,
        fallback: Optional[str] = None,
        tier: Optional[str] = None,
    ):
        """
        Args:
            model:    Pre-built model (used by tests to inject a fake). When supplied,
                      ``provider``/``fallback``/``tier`` are ignored.
            provider: Override the AI vendor for this instance (e.g. ``"groq"``).
                      Defaults to the ``MINDMORPH_TUTOR_PROVIDER`` env var, then the global default.
            fallback: Override the fallback vendor (e.g. ``"ollama"``).
                      Defaults to the ``MINDMORPH_TUTOR_FALLBACK`` env var, then the global default.
            tier:     Model-size tier: ``"default"`` or ``"complex"``.
                      Defaults to the ``MINDMORPH_TUTOR_TIER`` env var (default: ``"default"``).
        """
        self._model = model
        self._provider = provider or _TUTOR_PROVIDER   # instance arg > env var > global default
        self._fallback = fallback or _TUTOR_FALLBACK
        self._tier     = tier     or _TUTOR_TIER

    def _get_model(self) -> Any:
        if self._model is None:
            from config import get_chat_model

            # Route through the central Model Router (ProviderChain). Using the tutor-specific tier,
            # provider and fallback means the tutor can be pointed at a smarter model independently
            # of the rest of the system — just set MINDMORPH_TUTOR_TIER=complex, for example.
            self._model = get_chat_model(
                self._tier,
                provider=self._provider,
                fallback=self._fallback,
                temperature=TUTOR_TEMPERATURE,
            )
            logger.debug(
                "tutor: model resolved → tier=%r provider=%r fallback=%r",
                self._tier, self._provider, self._fallback,
            )
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
