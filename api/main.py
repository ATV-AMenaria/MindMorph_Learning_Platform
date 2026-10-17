"""FastAPI application entrypoint.

Run locally:  conda run -n mindmorph uvicorn api.main:app --reload
CORS is opened for the Next.js dev origin (Phase B); tighten ``MINDMORPH_CORS_ORIGINS`` in production.
"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Validate the LLM key the same way config.py does (the agents need it). Import surfaces the
# ValueError at startup rather than on the first request.
import config  # noqa: E402,F401

from api.routes import router  # noqa: E402

app = FastAPI(title="MindMorph API", version="1.0.0")

_origins = os.getenv("MINDMORPH_CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def startup_create_tables():
    """Auto-create tables from ORM models on startup (idempotent).

    Only runs when the active store is Postgres (the default). Lazy import so the
    in-memory path never needs a live database connection.
    """
    if os.getenv("MINDMORPH_STORE", "postgres").lower() == "memory":
        return
    try:
        from persistence.db import create_all

        create_all()
        logger.info("startup: database tables verified/created")
    except Exception:
        logger.warning("startup: could not verify DB tables — is PostgreSQL running?")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "store": os.getenv("MINDMORPH_STORE", "postgres")}
