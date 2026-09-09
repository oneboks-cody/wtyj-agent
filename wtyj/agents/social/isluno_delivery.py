"""One-attempt native sends, persistent ambiguity and recipient pacing."""
from shared.mermaid_maintenance import participating as _maintenance_participating
import hashlib
import json
import os
import time
from urllib.parse import quote

from agents.social.isluno_discovery import DiscoveryStore, dump
from shared.isluno_config import JourneyScope, require_scope
from shared.isluno_pricing import ItineraryError


@_maintenance_participating('transport')
def post_once(scope, body, key, guard=None):
    from agents.social import zernio_dm_client as client
    require_scope(scope)
    if guard is not None and guard() is not True:
        return {'status': 'blocked'}
    if body.get('accountId') != scope.account_id or not client._provider_mutation_account_allowed(scope.account_id, 'isluno_discovery'):
        return {'status': 'blocked'}
    api_key = os.environ.get('LATE_API_KEY', '')
    if not api_key:
        return {'status': 'blocked'}
    try:
        response = client.http_requests.post('https://zernio.com/api/v1/inbox/conversations/' + quote(scope.conversation_id, safe='') + '/messages',
            headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json', 'Idempotency-Key': key}, json=body, timeout=15)
    except client.http_requests.RequestException:
        return {'status': 'ambiguous'}  # No blind retry, even with an idempotency key.
    data = client._response_json(response)
    details = data.get('data') if isinstance(data.get('data'), dict) else {}
    if details.get('partialFailure') or data.get('warnings'):
        return {'status': 'ambiguous'}
    provider_id = client._response_message_id(response)
    if 200 <= response.status_code < 300:
        return {'status': 'accepted' if provider_id and data.get('success') is not False else 'ambiguous', 'provider_id': provider_id}
    # Documented errors use top-level string code and optional platformError.
    # Neither shape proves a safe no-send media failure: never auto-fallback.
    if response.status_code in {408, 409, 429} or response.status_code >= 500:
        return {'status': 'ambiguous'}
    return {'status': 'rejected'}


@_maintenance_participating('delivery')
def send_plan(conversation_id, account_id, plan_id, *, store=None, post=None, window=None, sleep=None):
    from agents.social import zernio_dm_client as client
    store = store or DiscoveryStore()
    post = post or (lambda scope, body, key: post_once(scope, body, key, guard=lambda: store.delivery_plan(plan_id, account_id, conversation_id)[0]['body'] == body))
    window = window or client.whatsapp_customer_service_window
    sleep = sleep or time.sleep
    try:
        plan, status = store.delivery_plan(plan_id, account_id, conversation_id)
        scope = JourneyScope(**plan['scope'])
        if status == 'accepted':
            return True
        if status != 'queued':
            return False  # Claimed/crashed or ambiguous attempts cannot be blindly resent.
        if window(conversation_id, account_id, plan['trigger_sent_at']).get('open') is not True:
            return False
        recipient = hashlib.sha256(dump([scope.tenant_slug, scope.account_id, scope.customer_ref]).encode()).hexdigest()
        for attempt in range(2):
            require_scope(scope)
            with store.db() as db, db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT status FROM isluno_discovery_plans WHERE id=?', (plan_id,)).fetchone()
                if row['status'] != 'queued':
                    return row['status'] == 'accepted'
                previous = db.execute('SELECT last_send FROM isluno_discovery_pacing WHERE recipient=?', (recipient,)).fetchone()
                now = store.clock().timestamp()
                wait = max(0, 6 - (now - previous[0])) if previous else 0
                if wait == 0:
                    db.execute("UPDATE isluno_discovery_plans SET status='claimed' WHERE id=?", (plan_id,))
                    db.execute('INSERT INTO isluno_discovery_pacing VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET last_send=excluded.last_send', (recipient, now))
                    break
            if attempt == 1:
                return False
            sleep(min(wait, 6))
        else:
            return False
        # Check the window again after pacing, immediately before dispatch.
        if window(conversation_id, account_id, plan['trigger_sent_at']).get('open') is not True:
            result = {'status': 'window_closed'}
        else:
            result = post(scope, plan['body'], 'isluno-discovery-' + plan_id + ('-fallback' if plan.get('fallback_active') else ''))
        # A remote rejection does not establish safe automatic fallback.
        with store.db() as db, db:
            db.execute('UPDATE isluno_discovery_plans SET status=?,provider_id=? WHERE id=?',
                       (result.get('status', 'ambiguous'), result.get('provider_id'), plan_id))
        return result.get('status') == 'accepted'
    except (ItineraryError, PermissionError):
        return False
