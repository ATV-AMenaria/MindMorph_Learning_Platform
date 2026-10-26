import logging
import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from llm_providers import ProviderChain

load_dotenv()

logger = logging.getLogger(__name__)

# Two backends, in preference order:
#
#   Groq (hosted, optional) — fast. Free-tier quota is metered per key, so GROQ_API_KEYS holds a
#     comma-separated pool and llm_providers.ProviderChain rotates through it on rate-limit/auth
#     errors. Model ids are pinned to the OpenAI OSS family; do NOT point these at a Llama model,
#     Groq deprecated all of them and that is what silently broke this project before.
#   Ollama (local, always available) — the zero-key path. The README's "no API key required" claim
#     rests on this: with GROQ_API_KEYS absent or empty the app runs on Ollama exactly as before.
#
# Constructing either client here doesn't touch the network (both connect lazily on first call), so
# importing this module stays offline/hermetic with no keys and no daemon running.
#
# Two tiers, mapped per provider:
#   "default" -> fast model for interactive beats (lesson gen, grading, tutor chat, routing)
#   "complex" -> larger model for reasoning-heavy, once-per-session work (skill-graph consensus,
#                review, scout query planning)
OLLAMA_MODEL = os.getenv("MINDMORPH_OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_MODEL_COMPLEX = os.getenv("MINDMORPH_OLLAMA_MODEL_COMPLEX", "qwen2.5:14b")
OLLAMA_HOST = os.getenv("MINDMORPH_OLLAMA_HOST", os.getenv("OLLAMA_HOST", "http://localhost:11434"))

GROQ_MODEL = os.getenv("MINDMORPH_GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_MODEL_COMPLEX = os.getenv("MINDMORPH_GROQ_MODEL_COMPLEX", GROQ_MODEL)

temperature_setting = 0.1

VALID_PROVIDERS = ("groq", "ollama")

TIER_MODELS = {
    "default": OLLAMA_MODEL,
    "complex": OLLAMA_MODEL_COMPLEX,
}
GROQ_TIER_MODELS = {
    "default": GROQ_MODEL,
    "complex": GROQ_MODEL_COMPLEX,
}


def groq_api_keys() -> list[str]:
    """The configured Groq key pool. Read from the environment on every call so tests can clear it."""
    return [key.strip() for key in os.getenv("GROQ_API_KEYS", "").split(",") if key.strip()]


def _ollama(tier: str, temperature: float) -> ChatOllama:
    return ChatOllama(
        model=TIER_MODELS.get(tier, OLLAMA_MODEL),
        base_url=OLLAMA_HOST,
        temperature=temperature,
    )


def _groq_pool(tier: str, temperature: float) -> list:
    """One ChatGroq per key — same model, separate quota bucket.

    ``max_retries=0`` so a 429 surfaces immediately and rotation happens at *our* layer; the SDK's
    own retry would otherwise sit on an exhausted key while four fresh ones go unused.

    Returns [] with no keys — and imports langchain-groq only once there is a key to use it with, so
    an existing checkout that hasn't reinstalled requirements still degrades to Ollama instead of
    dying on ImportError.
    """
    keys = groq_api_keys()
    if not keys:
        return []

    from langchain_groq import ChatGroq

    model_name = GROQ_TIER_MODELS.get(tier, GROQ_MODEL)
    return [
        ChatGroq(model=model_name, api_key=key, temperature=temperature, max_retries=0)
        for key in keys
    ]


def _build_model(name: str, tier: str, temperature: float):
    """Construct a single-provider chat model for a complexity tier."""
    name = name.lower()
    if name == "ollama":
        return _ollama(tier, temperature)
    if name == "groq":
        pool = _groq_pool(tier, temperature)
        if not pool:
            return None  # caller decides; no keys is not an error, it's the zero-key path
        return ProviderChain(pool)
    raise ValueError(f"Unknown LLM provider {name!r}; valid: {', '.join(VALID_PROVIDERS)}")


def get_chat_model(
    tier: str = "default",
    provider: str | None = None,
    fallback: str | None = None,
    temperature: float = temperature_setting,
):
    """Return the chat model for a complexity tier.

    - ``tier`` selects the model: "default" (fast) or "complex" (quality). Unrecognized -> default.
    - ``provider`` defaults to ``MINDMORPH_LLM_PROVIDER``, and to "groq" when keys are configured,
      "ollama" otherwise.
    - ``fallback`` defaults to ``MINDMORPH_LLM_FALLBACK`` ("ollama"). Only meaningful for Groq.
    - ``temperature`` defaults to the analytical 0.1 every structured-output caller wants; the tutor
      passes a higher value for conversational replies.

    Asking for Groq with no keys is *not* an error — it silently returns the local model, so a clone
    with no ``.env`` runs on Ollama alone.
    """
    keys = groq_api_keys()
    primary_name = (provider or os.getenv("MINDMORPH_LLM_PROVIDER") or ("groq" if keys else "ollama")).lower()
    primary = _build_model(primary_name, tier, temperature)
    if primary is None:
        logger.debug("llm: provider 'groq' requested but no GROQ_API_KEYS set — using local Ollama")
        return _ollama(tier, temperature)
    if isinstance(primary, ProviderChain):
        fallback_name = (fallback or os.getenv("MINDMORPH_LLM_FALLBACK", "ollama")).lower()
        if fallback_name and fallback_name != "none":
            primary.fallback = _build_model(fallback_name, tier, temperature)
    return primary


# Default-tier model reused across agents via `from config import llm`.
llm = get_chat_model("default")

# RAG grounding (P1 #7). Opt-in: enabling it builds a local FastEmbed-backed vector store from
# KNOWLEDGE_DIR on first content-graph build (downloads the embedding model once). Off by default so
# dev/tests never trigger a model download; retrieval augments web search, never replaces it.
RAG_ENABLED = os.getenv("MINDMORPH_RAG", "0").lower() in ("1", "true", "yes")
KNOWLEDGE_DIR = os.getenv("MINDMORPH_KNOWLEDGE_DIR", "knowledge_base")

# Persistence (P1 #6). Default targets the local docker-compose Postgres (see docker-compose.yml);
# override via DATABASE_URL in .env for any other deployment. Kept as a default (not a hard raise) so
# the agent/test paths that never touch the DB still import config cleanly.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://mindmorph:mindmorph@localhost:5432/mindmorph",
)
