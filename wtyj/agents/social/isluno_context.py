"""Additive, journey-scoped conversation context; never reads Mermaid intake."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from shared.isluno_config import LANGUAGES, brand_snapshot, require_scope


class ContextConflict(ValueError):
    pass


_STATE_FIELDS = {"chat_language", "document_language", "active_itinerary_id", "history", "reminder_preference", "last_inbound_at"}


def validate_state(state):
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        raise ValueError("Only the Isluno context contract can be stored")
    if not isinstance(state["chat_language"], str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?", state["chat_language"]):
        raise ValueError("Invalid chat language")
    if state["document_language"] is not None and (not isinstance(state["document_language"], str) or state["document_language"] not in LANGUAGES):
        raise ValueError("Unsupported document language")
    if not isinstance(state["reminder_preference"], str) or state["reminder_preference"] not in {"unchanged", "stop", "resume"}:
        raise ValueError("Invalid reminder preference")
    itinerary = state["active_itinerary_id"]
    if itinerary is not None and (not isinstance(itinerary, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", itinerary)):
        raise ValueError("Invalid itinerary reference")
    timestamp = state["last_inbound_at"]
    if timestamp is not None:
        if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp).tzinfo is None:
            raise ValueError("Inbound timestamp must have a timezone")
    history = state["history"]
    if not isinstance(history, list) or len(history) > 100:
        raise ValueError("Invalid context history")
    for item in history:
        if (not isinstance(item, dict) or set(item) != {"role", "content"}
                or not isinstance(item["role"], str) or item["role"] not in {"user", "assistant"}
                or not isinstance(item["content"], str) or not item["content"].strip()
                or len(item["content"]) > 12000):
            raise ValueError("Invalid context turn")
    return copy.deepcopy(state)


class ContextStore:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path is not None else Path(__file__).resolve().parents[2] / "data/state_registry.db"

    @contextmanager
    def _connection(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("""CREATE TABLE IF NOT EXISTS isluno_contexts (
                session_key TEXT PRIMARY KEY,
                tenant_slug TEXT NOT NULL CHECK(tenant_slug='mermaid'),
                account_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                customer_ref TEXT NOT NULL,
                journey_type TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                brand_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(tenant_slug, account_id, conversation_id, customer_ref, journey_type, schema_version)
            )""")
            connection.commit()
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _where(scope):
        return (scope.key, scope.tenant_slug, scope.account_id, scope.conversation_id,
                scope.customer_ref, scope.journey_type, scope.schema_version)

    @staticmethod
    def _select(connection, scope):
        return connection.execute("""SELECT * FROM isluno_contexts WHERE session_key=? AND tenant_slug=?
            AND account_id=? AND conversation_id=? AND customer_ref=? AND journey_type=? AND schema_version=?""",
                                  ContextStore._where(scope)).fetchone()

    @staticmethod
    def _decode(row):
        result = dict(row)
        result["brand_snapshot"] = json.loads(result.pop("brand_json"))
        result["state"] = validate_state(json.loads(result.pop("state_json")))
        return result

    def open(self, scope):
        profile = require_scope(scope)  # Before any filesystem/database side effect.
        with self._connection() as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._select(connection, scope)
                if row is None:
                    now = datetime.now(timezone.utc).isoformat()
                    state = {"chat_language": "en", "document_language": None, "active_itinerary_id": None,
                             "history": [], "reminder_preference": "unchanged", "last_inbound_at": None}
                    connection.execute("""INSERT INTO isluno_contexts
                        (session_key,tenant_slug,account_id,conversation_id,customer_ref,journey_type,schema_version,
                         revision,brand_json,state_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?,?,?)""",
                                       (*self._where(scope), json.dumps(brand_snapshot(profile)), json.dumps(state), now, now))
                    row = self._select(connection, scope)
                return self._decode(row)

    def save(self, scope, *, expected_revision, state):
        require_scope(scope)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ContextConflict("Expected context revision is required")
        state = validate_state(state)
        with self._connection() as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._select(connection, scope)
                if row is None or row["revision"] != expected_revision:
                    raise ContextConflict("Context changed; reload before updating")
                connection.execute("""UPDATE isluno_contexts SET state_json=?,revision=revision+1,updated_at=?
                    WHERE session_key=? AND revision=?""",
                                   (json.dumps(state), datetime.now(timezone.utc).isoformat(), scope.key, expected_revision))
                return self._decode(self._select(connection, scope))

    def append_turn(self, scope, *, expected_revision, role, content, inbound_at=None):
        current = self.open(scope)
        if current["revision"] != expected_revision:
            raise ContextConflict("Context changed; reload before appending")
        state = current["state"]
        state["history"] = (state["history"] + [{"role": role, "content": content}])[-100:]
        if inbound_at is not None:
            if role != "user":
                raise ValueError("Only a verified guest turn updates inbound time")
            state["last_inbound_at"] = inbound_at
        return self.save(scope, expected_revision=expected_revision, state=state)
