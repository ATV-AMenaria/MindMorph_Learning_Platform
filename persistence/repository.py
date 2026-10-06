"""Repository for learning sessions (Repository pattern — see common/patterns.md).

The abstract ``LearningSessionRepository`` is the only interface the service/API layer depends on, so
the Postgres engine stays swappable (a future Redis/in-memory impl, or a different DB, drops in without
touching callers). The Postgres implementation persists each session as a single JSONB row.
"""
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Safety cap for list_all() — it loads full session blobs into memory (operator/analytics only).
LIST_ALL_LIMIT = 5000


class LearningSessionRepository(ABC):
    """Storage-agnostic interface for persisting ``learning_session`` dicts."""

    @abstractmethod
    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        """Return the stored learning_session dict, or None if absent."""

    @abstractmethod
    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        """Upsert the learning_session dict. ``title`` is set on insert; preserved if None on update."""

    @abstractmethod
    def list_sessions(self, user_id: str) -> list[dict]:
        """Return session metadata for a user, most-recently-updated first."""

    @abstractmethod
    def delete(self, user_id: str, session_id: str) -> None:
        """Remove a session (no-op if absent)."""

    @abstractmethod
    def list_all(self) -> list[tuple[str, str, dict]]:
        """Return every stored session as (user_id, session_id, learning_session). Operator/analytics
        use only — loads full blobs across all users, so callers must gate access (see /admin/funnel)."""


class PostgresLearningSessionRepository(LearningSessionRepository):
    """Production store: one JSONB row per session. SQLAlchemy/Postgres imports are done lazily so
    importing this module (e.g. for the in-memory store, or in tests) needs no live database."""

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow

        with get_sessionmaker()() as session:
            row = session.get(LearningSessionRow, (user_id, session_id))
            return dict(row.data) if row is not None else None

    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        # Postgres upsert: insert the blob, or overwrite ``data`` on (user_id, session_id) conflict.
        # On update we always refresh data + updated_at; ``title`` is only overwritten when a non-empty
        # one is supplied, so a later save() that omits the title never wipes the existing one.
        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow

        stmt = pg_insert(LearningSessionRow).values(
            user_id=user_id, session_id=session_id, data=ls, title=title or "",
            created_at=func.now(), updated_at=func.now(),
        )
        update_set = {"data": stmt.excluded.data, "updated_at": func.now()}
        if title:
            update_set["title"] = stmt.excluded.title
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "session_id"], set_=update_set
        )
        with get_sessionmaker()() as session:
            session.execute(stmt)
            session.commit()
        logger.info("persistence: saved session %s/%s", user_id, session_id)
        
        # Phase 3.4: Sync progress to Neo4j
        try:
            from persistence.neo4j_graph import write_session_progress
            write_session_progress(session_id, ls.get("node_state", {}))
        except Exception:
            pass

    def list_sessions(self, user_id: str) -> list[dict]:
        from sqlalchemy import select

        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow

        with get_sessionmaker()() as session:
            rows = session.execute(
                select(LearningSessionRow)
                .where(LearningSessionRow.user_id == user_id)
                .order_by(LearningSessionRow.updated_at.desc())
            ).scalars().all()
            return [
                {
                    "session_id": r.session_id,
                    "title": r.title,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ]

    def delete(self, user_id: str, session_id: str) -> None:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow

        with get_sessionmaker()() as session:
            row = session.get(LearningSessionRow, (user_id, session_id))
            if row is not None:
                session.delete(row)
                session.commit()

    def list_all(self) -> list[tuple[str, str, dict]]:
        from sqlalchemy import select

        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow

        # Bounded: this materializes full JSONB blobs in memory, so cap it (operator/analytics use at
        # prototype scale). A real analytics path would page or stream; if the cap is hit the
        # /admin/funnel aggregate is a lower bound — fine for Gate-1 directional signal.
        with get_sessionmaker()() as session:
            rows = (
                session.execute(select(LearningSessionRow).limit(LIST_ALL_LIMIT)).scalars().all()
            )
            return [(r.user_id, r.session_id, dict(r.data)) for r in rows]


class InMemoryLearningSessionRepository(LearningSessionRepository):
    """Process-local store (no infra). Doubles as the test store and a dev fallback when no Postgres
    is available. NOT durable across process restarts — use Postgres for real persistence.

    Stored dicts are round-tripped through ``json.dumps/loads`` (NOT deepcopy) on the way in: this both
    isolates persisted state from caller mutation AND faithfully mirrors the JSONB store — a value the
    real Postgres column would reject (a Pydantic model, datetime, set …) raises here too, so the dev
    store can't pass something production would fail on.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        row = self._store.get((user_id, session_id))
        return json.loads(row["data"]) if row is not None else None

    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        key = (user_id, session_id)
        existing = self._store.get(key)
        kept_title = existing["title"] if (existing and not title) else (title or "")
        self._store[key] = {
            "data": json.dumps(ls),  # raises TypeError on any non-JSON-serializable value (like JSONB)
            "title": kept_title,
            "updated_at": datetime.now(timezone.utc),
        }
        
        # Phase 3.4: Sync progress to Neo4j
        try:
            from persistence.neo4j_graph import write_session_progress
            write_session_progress(session_id, ls.get("node_state", {}))
        except Exception:
            pass

    def list_sessions(self, user_id: str) -> list[dict]:
        rows = [
            (sid, row) for (uid, sid), row in self._store.items() if uid == user_id
        ]
        rows.sort(key=lambda t: t[1]["updated_at"], reverse=True)
        return [
            {"session_id": sid, "title": row["title"], "updated_at": row["updated_at"].isoformat()}
            for sid, row in rows
        ]

    def delete(self, user_id: str, session_id: str) -> None:
        self._store.pop((user_id, session_id), None)

    def list_all(self) -> list[tuple[str, str, dict]]:
        return [
            (uid, sid, json.loads(row["data"])) for (uid, sid), row in self._store.items()
        ]


# A single in-memory instance shared across the process when the memory store is selected, so the API
# and any same-process caller see the same data (the dict must outlive a single request).
_memory_singleton = InMemoryLearningSessionRepository()


def get_default_repository() -> LearningSessionRepository:
    """Return the configured repository. ``MINDMORPH_STORE=memory`` selects the in-memory store
    (zero-infra dev/test); anything else (default) uses Postgres."""
    store = os.getenv("MINDMORPH_STORE", "postgres").lower()
    if store == "memory":
        return _memory_singleton
    return PostgresLearningSessionRepository()
