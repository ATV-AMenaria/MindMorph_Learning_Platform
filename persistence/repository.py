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
from threading import RLock
from functools import wraps

from persistence.errors import SessionSnapshot, SessionConflictError, StorageUnavailableError

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
        """Insert a new dict, or save a snapshot returned by get().

        Updates compare the snapshot's revision; stale snapshots and unversioned overwrites raise
        SessionConflictError. Missing titles preserve the existing label.
        """

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


def _storage_errors(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        from sqlalchemy.exc import SQLAlchemyError
        try:
            return method(*args, **kwargs)
        except SQLAlchemyError as exc:
            logger.exception("Database operation failed: %s", method.__name__)
            raise StorageUnavailableError("Progress could not be saved or loaded. Please try again.") from exc
    return wrapped


class PostgresLearningSessionRepository(LearningSessionRepository):
    """Durable storage only. Updates compare the version read by the caller."""

    @_storage_errors
    def get(self, user_id, session_id):
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow
        with get_sessionmaker()() as session:
            row = session.get(LearningSessionRow, (user_id, session_id))
            return SessionSnapshot(row.data, row.revision) if row is not None else None

    @_storage_errors
    def save(self, user_id, session_id, ls, title=None):
        from sqlalchemy import update, func
        from sqlalchemy.dialects.postgresql import insert
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        expected = getattr(ls, "revision", None)
        if expected is None:
            stmt = insert(Row).values(user_id=user_id, session_id=session_id, data=dict(ls),
                                      title=title or "", revision=1).on_conflict_do_nothing()
        else:
            values = {"data": dict(ls), "updated_at": func.now(), "revision": Row.revision + 1}
            if title is not None:
                values["title"] = title
            stmt = update(Row).where(Row.user_id == user_id, Row.session_id == session_id,
                                     Row.revision == expected).values(**values)
        with get_sessionmaker()() as session:
            revision = session.execute(stmt.returning(Row.revision)).scalar_one_or_none()
            if revision is None:
                raise SessionConflictError("Your progress changed in another action. Reload the path before trying again.")
            session.commit()
        if isinstance(ls, SessionSnapshot):
            ls.revision = revision
        logger.info("persistence: saved session %s/%s", user_id, session_id)

        # Phase 3.4: Sync progress to Neo4j
        try:
            from persistence.neo4j_graph import write_session_progress
            write_session_progress(session_id, ls.get("node_state", {}))
        except Exception:
            pass

    @_storage_errors
    def list_sessions(self, user_id):
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        with get_sessionmaker()() as session:
            rows = session.execute(select(Row).where(Row.user_id == user_id)
                                   .order_by(Row.updated_at.desc(), Row.session_id)).scalars().all()
            return [{"session_id": r.session_id, "title": r.title,
                     "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]

    @_storage_errors
    def delete(self, user_id, session_id):
        from sqlalchemy import delete
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        with get_sessionmaker()() as session:
            session.execute(delete(Row).where(Row.user_id == user_id, Row.session_id == session_id))
            session.commit()

    @_storage_errors
    def list_all(self):
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        with get_sessionmaker()() as session:
            rows = session.execute(select(Row).limit(LIST_ALL_LIMIT)).scalars().all()
            return [(r.user_id, r.session_id, dict(r.data)) for r in rows]


class InMemoryLearningSessionRepository(LearningSessionRepository):
    """Explicit process-local development/test store. Never a hidden database fallback.
    NOT durable across process restarts — use Postgres for real persistence.

    Stored dicts are round-tripped through ``json.dumps/loads`` (NOT deepcopy) on the way in: this both
    isolates persisted state from caller mutation AND faithfully mirrors the JSONB store — a value the
    real Postgres column would reject (a Pydantic model, datetime, set …) raises here too, so the dev
    store can't pass something production would fail on.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._store: dict[tuple[str, str], dict] = {}
        self._save_sequence = 0

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._store.get((user_id, session_id))
            return SessionSnapshot(json.loads(row["data"]), row["revision"]) if row is not None else None

    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        data = json.dumps(ls)
        with self._lock:
            key = (user_id, session_id)
            existing = self._store.get(key)
            expected = getattr(ls, "revision", None)
            current = existing["revision"] if existing else None
            if current != expected:
                raise SessionConflictError("Your progress changed in another action. Reload the path before trying again.")
            revision = (current or 0) + 1
            kept_title = existing["title"] if existing and title is None else (title or "")
            self._save_sequence += 1
            self._store[key] = {
                "data": data, "title": kept_title, "updated_at": datetime.now(timezone.utc),
                "save_sequence": self._save_sequence, "revision": revision,
            }
            if isinstance(ls, SessionSnapshot):
                ls.revision = revision

        # Phase 3.4: Sync progress to Neo4j
        try:
            from persistence.neo4j_graph import write_session_progress
            write_session_progress(session_id, ls.get("node_state", {}))
        except Exception:
            pass

    def list_sessions(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = [(sid, row.copy()) for (uid, sid), row in self._store.items() if uid == user_id]
        rows.sort(key=lambda t: (t[1]["updated_at"], t[1]["save_sequence"]), reverse=True)
        return [
            {"session_id": sid, "title": row["title"], "updated_at": row["updated_at"].isoformat()}
            for sid, row in rows
        ]

    def delete(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._store.pop((user_id, session_id), None)

    def list_all(self) -> list[tuple[str, str, dict]]:
        with self._lock:
            return [(uid, sid, json.loads(row["data"])) for (uid, sid), row in self._store.items()]


# A single in-memory instance shared across the process when the memory store is selected, so the API
# and any same-process caller see the same data (the dict must outlive a single request).
_memory_singleton = InMemoryLearningSessionRepository()


def get_default_repository() -> LearningSessionRepository:
    """Return the configured repository. ``MINDMORPH_STORE=memory`` selects the in-memory store
    (zero-infra dev/test); ``postgres`` (default) selects durable storage."""
    store = os.getenv("MINDMORPH_STORE", "postgres").lower()
    if store == "memory":
        return _memory_singleton
    if store != "postgres":
        raise StorageUnavailableError("Unknown storage mode. Select memory or postgres.")
    return PostgresLearningSessionRepository()
