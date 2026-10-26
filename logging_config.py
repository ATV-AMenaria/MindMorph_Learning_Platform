"""Central logging configuration for MindMorph.

Libraries (agents/graphs/tools) only call `logging.getLogger(__name__)` and log; they never
configure handlers. The application entry point (app.py) calls `setup_logging()` once at startup.
Level is controlled by the MINDMORPH_LOG_LEVEL env var (default INFO).

Logs go to both the console (stderr) and a rotating file at logs/mindmorph.log.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "mindmorph.log")
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured = False


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once (idempotent). Safe to call from every entry point."""
    global _configured
    if _configured:
        return

    resolved = (level or os.getenv("MINDMORPH_LOG_LEVEL", "INFO")).upper()
    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(resolved)
    root.handlers.clear()  # avoid duplicate handlers on Streamlit reruns / re-imports
    root.addHandler(console)
    root.addHandler(file_handler)

    # Tame chatty third-party loggers so app signal isn't drowned out.
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "ollama"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    root.debug("Logging configured at level %s -> %s", resolved, LOG_FILE)
