"""Scoped demo itineraries with immutable revisions and transactional replay."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from shared import config_loader
from shared.isluno_catalog import CatalogStore
from shared.isluno_config import brand_snapshot, require_scope
from shared.isluno_pricing import ItineraryError, check, identifier, price_item, totals


class ItineraryConflict(ItineraryError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class ItineraryStore:
    def __init__(self, db_path=None, catalog_path=None, clock=None):
        self.db_path = Path(db_path) if db_path is not None else Path(__file__).resolve().parents[2] / 'data/state_registry.db'
        self.catalog_path = Path(catalog_path) if catalog_path is not None else Path(config_loader._CONFIG_PATH).with_name('isluno_catalog.json')
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def _connection(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS isluno_itineraries (
                    scope_key TEXT NOT NULL, itinerary_id TEXT NOT NULL, scope_json TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision > 0), payload_json TEXT NOT NULL,
                    PRIMARY KEY(scope_key, itinerary_id)
                );
                CREATE TABLE IF NOT EXISTS isluno_itinerary_versions (
                    scope_key TEXT NOT NULL, itinerary_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(scope_key, itinerary_id, revision),
                    FOREIGN KEY(scope_key, itinerary_id) REFERENCES isluno_itineraries(scope_key, itinerary_id)
                );
                CREATE TABLE IF NOT EXISTS isluno_itinerary_requests (
                    scope_key TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL, PRIMARY KEY(scope_key, request_id)
                );
                CREATE TRIGGER IF NOT EXISTS isluno_versions_no_update BEFORE UPDATE ON isluno_itinerary_versions
                    BEGIN SELECT RAISE(ABORT, 'immutable itinerary revision'); END;
                CREATE TRIGGER IF NOT EXISTS isluno_versions_no_delete BEFORE DELETE ON isluno_itinerary_versions
                    BEGIN SELECT RAISE(ABORT, 'immutable itinerary revision'); END;
            ''')
            yield db
        finally:
            db.close()

    @staticmethod
    def _scope(scope):
        return encoded({field: getattr(scope, field) for field in
                        ('tenant_slug', 'account_id', 'conversation_id', 'customer_ref', 'journey_type', 'schema_version')})

    def _current(self, db, scope, itinerary_id):
        row = db.execute('SELECT * FROM isluno_itineraries WHERE scope_key=? AND itinerary_id=? AND scope_json=?',
                         (scope.key, itinerary_id, self._scope(scope))).fetchone()
        check(row is not None, 'itinerary_not_found')
        return json.loads(row['payload_json'])

    def get(self, scope, itinerary_id, *, revision=None):
        require_scope(scope)
        identifier(itinerary_id)
        check(revision is None or type(revision) is int and revision > 0, 'invalid_revision')
        with self._connection() as db:
            current = self._current(db, scope, itinerary_id)
            if revision is None:
                return current
            row = db.execute('SELECT payload_json FROM isluno_itinerary_versions WHERE scope_key=? AND itinerary_id=? AND revision=?',
                             (scope.key, itinerary_id, revision)).fetchone()
            check(row is not None, 'revision_not_found')
            return json.loads(row['payload_json'])

    def summaries(self, scope, *, limit=50, offset=0):
        require_scope(scope)
        check(type(limit) is int and 1 <= limit <= 100 and type(offset) is int and offset >= 0, 'invalid_pagination')
        with self._connection() as db:
            rows = db.execute('SELECT payload_json FROM isluno_itineraries WHERE scope_key=? AND scope_json=? ORDER BY itinerary_id LIMIT ? OFFSET ?',
                              (scope.key, self._scope(scope), limit, offset)).fetchall()
            return [{'id': p['id'], 'revision': p['revision'], 'status': p['status'], 'item_count': len(p['items']),
                     'totals': p['totals'], 'updated_at': p['updated_at']}
                    for p in (json.loads(row[0]) for row in rows)]

    def create(self, scope, itinerary_id, *, request_id):
        return self._apply(scope, itinerary_id, request_id, None, {'action': 'create'})

    def mutate(self, scope, itinerary_id, *, request_id, expected_revision, mutation):
        check(type(expected_revision) is int and expected_revision > 0, 'invalid_revision')
        check(isinstance(mutation, dict) and isinstance(mutation.get('action'), str), 'invalid_mutation')
        action = mutation['action']
        check(action in {'add', 'update', 'remove'}, 'invalid_mutation')
        check(set(mutation) == ({'action', 'item_id'} if action == 'remove' else {'action', 'selection'}), 'invalid_mutation_fields')
        return self._apply(scope, itinerary_id, request_id, expected_revision, mutation)

    def _apply(self, scope, itinerary_id, request_id, expected_revision, mutation, *, connection=None, catalog_snapshot=None):
        profile = require_scope(scope)  # No database/config write before verified scope.
        identifier(itinerary_id)
        identifier(request_id)
        try:
            fingerprint = hashlib.sha256(encoded([itinerary_id, expected_revision, mutation]).encode()).hexdigest()
        except (TypeError, ValueError) as exc:
            raise ItineraryError('invalid_mutation') from exc
        with (self._connection() if connection is None else nullcontext(connection)) as db, (db if connection is None else nullcontext()):
            if connection is None:
                db.execute('BEGIN IMMEDIATE')
            replay = db.execute('SELECT * FROM isluno_itinerary_requests WHERE scope_key=? AND request_id=?',
                                (scope.key, request_id)).fetchone()
            if replay:
                if replay['fingerprint'] != fingerprint:
                    raise ItineraryConflict('request_id_reused_with_different_payload')
                return json.loads(replay['response_json'])
            now = self.clock()
            check(now.tzinfo is not None, 'clock_requires_timezone')
            action = mutation['action']
            if action == 'create':
                exists = db.execute('SELECT 1 FROM isluno_itineraries WHERE scope_key=? AND itinerary_id=?',
                                    (scope.key, itinerary_id)).fetchone()
                if exists:
                    raise ItineraryConflict('itinerary_already_exists')
                current = {'id': itinerary_id, 'schema_version': scope.schema_version, 'journey_type': scope.journey_type,
                           'brand_snapshot': brand_snapshot(profile), 'revision': 1, 'status': 'draft', 'items': [],
                           'totals': totals([]), 'created_at': now.isoformat(), 'updated_at': now.isoformat()}
                db.execute('INSERT INTO isluno_itineraries VALUES(?,?,?,?,?)',
                           (scope.key, itinerary_id, self._scope(scope), 1, encoded(current)))
            else:
                current = self._current(db, scope, itinerary_id)
                if current['revision'] != expected_revision:
                    raise ItineraryConflict('itinerary_revision_changed', current_revision=current['revision'])
                check(current['status'] == 'draft', 'itinerary_not_editable')
                items = current['items']
                if action == 'cancel':
                    items = current['items']
                    current['status'] = 'cancelled'
                elif action == 'remove':
                    item_id = identifier(mutation['item_id'])
                    check(any(i['id'] == item_id for i in items), 'item_not_found')
                    items = [i for i in items if i['id'] != item_id]
                else:
                    item = price_item(catalog_snapshot if catalog_snapshot is not None else CatalogStore(self.catalog_path).snapshot(), mutation['selection'], now=now)
                    exists = any(i['id'] == item['id'] for i in items)
                    check(not exists if action == 'add' else exists,
                          'item_already_exists' if action == 'add' else 'item_not_found')
                    items = items + [item] if action == 'add' else [item if i['id'] == item['id'] else i for i in items]
                current.update(items=items, totals=totals(items), revision=current['revision'] + 1, updated_at=now.isoformat())
                db.execute('UPDATE isluno_itineraries SET revision=?,payload_json=? WHERE scope_key=? AND itinerary_id=?',
                           (current['revision'], encoded(current), scope.key, itinerary_id))
            serialized = encoded(current)
            db.execute('INSERT INTO isluno_itinerary_versions VALUES(?,?,?,?)',
                       (scope.key, itinerary_id, current['revision'], serialized))
            db.execute('INSERT INTO isluno_itinerary_requests VALUES(?,?,?,?)',
                       (scope.key, request_id, fingerprint, serialized))
            return json.loads(serialized)
