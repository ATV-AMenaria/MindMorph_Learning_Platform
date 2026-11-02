"""Private evaluator packages and accepted attempts, scoped to the session repository.

Memory state belongs to each repository instance; PostgreSQL records share the session
transaction for progress application. No evaluator material lives in public DTOs.
"""
import json
import time
import uuid
from datetime import datetime, timezone

from persistence.errors import SessionConflictError, SessionSnapshot
from persistence.repository import InMemoryLearningSessionRepository, _storage_errors, _sync_progress_to_neo4j


def clone(value):
    return json.loads(json.dumps(value, allow_nan=False))


class GradingStore:
    def __init__(self, repo):
        self.repo = repo
        self.memory = isinstance(repo, InMemoryLearningSessionRepository)
        if self.memory:
            with repo._lock:
                if not hasattr(repo, "_grading_records"):
                    repo._grading_records = {}

    @_storage_errors
    def get(self, user, key):
        if self.memory:
            with self.repo._lock:
                row = self.repo._grading_records.get((user, key))
                return clone(row) if row else None
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow
        with get_sessionmaker()() as db:
            row = db.get(GradingRecordRow, (user, key))
            return clone(row.data) if row else None

    @_storage_errors
    def put_once(self, user, key, kind, data):
        """Atomically accept a binding before dispatch; return (record, newly_created)."""
        data = clone({**data, "kind": kind})
        if self.memory:
            with self.repo._lock:
                existing = self.repo._grading_records.get((user, key))
                if existing is not None:
                    return clone(existing), False
                self.repo._grading_records[(user, key)] = data
                return clone(data), True
        from sqlalchemy.dialects.postgresql import insert
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            created = db.execute(insert(Row).values(user_id=user, record_id=key, kind=kind, data=data)
                .on_conflict_do_nothing().returning(Row.record_id)).scalar_one_or_none()
            row = db.get(Row, (user, key))
            out = clone(row.data)
            db.commit()
            return out, created is not None

    @_storage_errors
    def update(self, user, key, changes, *, expected_status=None, expected_token=None):
        """Fence completion against the accepted execution token/status."""
        if self.memory:
            with self.repo._lock:
                row = self.repo._grading_records[(user, key)]
                if expected_status is not None and row.get("state") != expected_status:
                    return clone(row)
                if expected_token is not None and row.get("lease_token") != expected_token:
                    return clone(row)
                row.update(clone(changes))
                return clone(row)
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            row = db.execute(select(Row).where(Row.user_id == user, Row.record_id == key).with_for_update()).scalar_one()
            if ((expected_status is None or row.data.get("state") == expected_status)
                    and (expected_token is None or row.data.get("lease_token") == expected_token)):
                row.data = clone({**row.data, **changes})
            out = clone(row.data)
            db.commit()
            return out

    @_storage_errors
    def claim_generation(self, user, key, *, seconds=600, states=None):
        """Recover an abandoned generation; fence any late producer's writes."""
        def claim(record):
            if record.get("state") not in (states or {"accepted", "retryable_error"}):
                return record, False
            if record.get("state") != "retryable_error" and record.get("lease_until", 0) > time.time():
                return record, False
            record.update(state="accepted", lease_token=uuid.uuid4().hex, lease_until=time.time() + seconds)
            return record, True
        if self.memory:
            with self.repo._lock:
                record, accepted = claim(self.repo._grading_records[(user, key)])
                return clone(record), accepted
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            row = db.execute(select(Row).where(Row.user_id == user, Row.record_id == key).with_for_update()).scalar_one()
            record, accepted = claim(clone(row.data))
            row.data = clone(record)
            db.commit()
            return record, accepted

    @_storage_errors
    def list(self, user, kind):
        if self.memory:
            with self.repo._lock:
                return [clone(v) for (u, _), v in self.repo._grading_records.items() if u == user and v["kind"] == kind]
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            return [clone(row.data) for row in db.execute(select(Row).where(Row.user_id == user, Row.kind == kind)
                .order_by(Row.record_id).limit(5000)).scalars()]

    @_storage_errors
    def claim(self, user, key, expected, replacement):
        """CAS state transition, returning whether this controller won admission."""
        if self.memory:
            with self.repo._lock:
                row = self.repo._grading_records[(user, key)]
                if row.get("state") != expected:
                    return False
                row["state"] = replacement
                return True
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            row = db.execute(select(Row).where(Row.user_id == user, Row.record_id == key).with_for_update()).scalar_one()
            if row.data.get("state") != expected:
                return False
            row.data = {**row.data, "state": replacement}
            db.commit()
            return True

    @_storage_errors
    def admit_attempt(self, user, key):
        """Bounded admission with a renewable owner token and recoverable deadline.

        PostgreSQL's transaction advisory lock serializes admission across API
        processes. Workers may finish only while their current fenced token owns
        the record. Expired queued/running records cannot occupy capacity forever.
        """
        now = time.time()
        def admit(record, records):
            if record.get("state") == "completed":
                return record, False
            if record.get("state") == "running" and record.get("lease_until", 0) > now:
                return record, False
            if record.get("state") == "queued" and record.get("queue_until", 0) <= now:
                record.update(state="completed", result={"status": "retryable_error", "method": "execution",
                    "score": None, "mastery_eligible": False, "eligible": False,
                    "feedback": "The assessment queue deadline expired. Submit a new attempt to retry."})
                return record, False
            active = [(uid, row) for uid, row in records if row.get("state") == "running" and row.get("lease_until", 0) > now]
            requested_memory = record.get('memory_mb', 512)
            active_memory = sum(row.get('memory_mb', 512) for _, row in active)
            if len(active) >= 2 or any(uid == user for uid, _ in active) or active_memory + requested_memory > 2048:
                queued = [row for _, row in records if row.get("state") == "queued" and row.get("queue_until", 0) > now]
                if len(queued) >= 8 and record.get("state") != "queued":
                    record.update(state="completed", result={"status": "retryable_error", "method": "execution",
                        "score": None, "mastery_eligible": False, "eligible": False,
                        "feedback": "Assessment capacity is full. Submit a new attempt shortly."})
                else:
                    record.update(state="queued", queue_until=record.get("queue_until", now + 30))
                return record, False
            record.update(state="running", lease_token=uuid.uuid4().hex, lease_until=now + 120,
                dispatches=record.get("dispatches", 0) + 1)
            return record, True
        if self.memory:
            with self.repo._lock:
                record = self.repo._grading_records[(user, key)]
                records = [(u, row) for (u, _), row in self.repo._grading_records.items() if row["kind"] in {"attempt", "run"}]
                record, claimed = admit(record, records)
                return clone(record), claimed
        from sqlalchemy import select, text, and_, or_, Float
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('mindmorph-grading-admission'))"))
            live = or_(and_(Row.data['state'].astext == 'running', Row.data['lease_until'].astext.cast(Float) > now),
                       and_(Row.data['state'].astext == 'queued', Row.data['queue_until'].astext.cast(Float) > now))
            target_key = and_(Row.user_id == user, Row.record_id == key)
            rows = db.execute(select(Row).where(Row.kind.in_(["attempt", "run"]), or_(target_key, live)).with_for_update()).scalars().all()
            target = next(row for row in rows if row.user_id == user and row.record_id == key)
            record, claimed = admit(clone(target.data), [(row.user_id, row.data) for row in rows])
            target.data = clone(record)
            db.commit()
            return record, claimed

    @_storage_errors
    def complete_attempt(self, user, key, token, result):
        """Late controllers cannot replace a reclaimed or completed result."""
        now = time.time()
        def completed(record):
            if record.get("state") == "running" and record.get("lease_token") == token and record.get("lease_until", 0) > now:
                record.update(state="completed", result=clone(result), completed_at=datetime.now(timezone.utc).isoformat())
            return record
        if self.memory:
            with self.repo._lock:
                return clone(completed(self.repo._grading_records[(user, key)]))
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow as Row
        with get_sessionmaker()() as db:
            row = db.execute(select(Row).where(Row.user_id == user, Row.record_id == key).with_for_update()).scalar_one()
            row.data = clone(completed(clone(row.data)))
            result = clone(row.data)
            db.commit()
            return result

    @_storage_errors
    def apply_session(self, user, sid, ls, attempt_key, new_node_ids):
        """Atomically save optimistic session revision and applied marker exactly once."""
        expected = getattr(ls, "revision", None)
        if self.memory:
            with self.repo._lock:
                attempt = self.repo._grading_records[(user, attempt_key)]
                if attempt.get("applied"):
                    return False
                if attempt.get("state") != "completed":
                    raise SessionConflictError("The assessment has not completed.")
                self.repo.save(user, sid, ls)
                attempt.update(applied=True, new_node_ids=clone(new_node_ids))
                return True
        from sqlalchemy import select, update, func
        from persistence.db import get_sessionmaker
        from persistence.models import GradingRecordRow, LearningSessionRow as Row
        with get_sessionmaker()() as db:
            attempt = db.execute(select(GradingRecordRow).where(GradingRecordRow.user_id == user,
                GradingRecordRow.record_id == attempt_key).with_for_update()).scalar_one()
            if attempt.data.get("applied"):
                return False
            if attempt.data.get("state") != "completed":
                raise SessionConflictError("The assessment has not completed.")
            revision = db.execute(update(Row).where(Row.user_id == user, Row.session_id == sid,
                Row.revision == expected).values(data=dict(ls), revision=Row.revision + 1,
                updated_at=func.now()).returning(Row.revision)).scalar_one_or_none()
            if revision is None:
                raise SessionConflictError("Assessment saved, but progress changed. Reload to reconcile this result.")
            attempt.data = clone({**attempt.data, "applied": True, "new_node_ids": new_node_ids})
            db.commit()
        if isinstance(ls, SessionSnapshot):
            ls.revision = revision
        _sync_progress_to_neo4j(sid, ls, user, revision)
        return True
