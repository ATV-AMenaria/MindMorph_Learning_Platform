"""Local Laya Decision Router for MindMorph Orchestration.

Provides sub-40ms, zero-cost, local classification over the three MindMorph routes:
  - SCOUT: New comprehensive learning plan / roadmap decomposition.
  - CONTENT: Specific lesson / explanation / topic breakdown.
  - EXERCISE: Practice challenge / coding exercise / quiz.

Operates as the PRIMARY router, gracefully returning None on any failure so
OrchestratorAgent can seamlessly fall back to the LLM-backed routing logic.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

VALID_ROUTES = ("SCOUT", "CONTENT", "EXERCISE")
_laya_classifier = None
_laya_initialized = False


def is_laya_enabled() -> bool:
    """Check if Laya router is enabled via environment variable (default: enabled)."""
    return os.getenv("MINDMORPH_USE_LAYA", "1").strip().lower() not in ("0", "false", "no")


def get_laya_classifier():
    """Lazy-load the Laya local classifier. Cached after first load."""
    global _laya_classifier, _laya_initialized
    if _laya_initialized:
        return _laya_classifier

    _laya_initialized = True
    if not is_laya_enabled():
        logger.info("Laya local router disabled via MINDMORPH_USE_LAYA=0")
        return None

    try:
        from laya import LayaClassifier

        model_name = os.getenv("MINDMORPH_LAYA_MODEL", "convaiinnovations/laya")
        logger.info("Initializing local Laya model (%s)...", model_name)
        _laya_classifier = LayaClassifier.from_pretrained(model_name)
        logger.info("Local Laya model loaded successfully.")
    except ImportError:
        logger.info(
            "Laya package is not installed (run 'pip install laya' to enable sub-40ms local routing). "
            "Orchestrator will use LLM fallback."
        )
        _laya_classifier = None
    except Exception as e:
        logger.warning("Could not load local Laya model (%s). Orchestrator will use LLM fallback.", e)
        _laya_classifier = None

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


@traceable(name="laya_local_router", run_type="chain")
def route_with_laya(query: str) -> Optional[str]:
    """Classify user query into SCOUT, CONTENT, or EXERCISE using local Laya.

    Returns:
        One of 'SCOUT', 'CONTENT', 'EXERCISE', or None if Laya is unavailable
        or fails, signaling the caller to use the LLM fallback.
    """
    if not is_laya_enabled():
        return None

    classifier = get_laya_classifier()
    if classifier is None:
        return None

    try:
        # Prompt framed for MindMorph's 3-way route decision
        result = classifier.predict_choice(
            text=query,
            question="Which agent must handle this user request? "
                     "SCOUT for creating a new curriculum roadmap, "
                     "CONTENT for explaining a specific lesson, "
                     "EXERCISE for coding challenges or quizzes.",
            choices=list(VALID_ROUTES),
        )

        choice = getattr(result, "best_choice", None)
        if choice is None and isinstance(result, str):
            choice = result
        elif choice is None and isinstance(result, dict):
            choice = result.get("choice") or result.get("best_choice")

        if choice in VALID_ROUTES:
            confidence = getattr(result, "confidence", None)
            conf_str = f" (confidence: {confidence:.2f})" if isinstance(confidence, (int, float)) else ""
            logger.info("Laya Primary Router: routed to %s%s", choice, conf_str)
            return choice

        logger.warning("Laya returned unexpected route %r; delegating to LLM fallback.", choice)
        return None
    except Exception as e:
        logger.warning("Laya classification error (%s); delegating to LLM fallback.", e)
        return None
