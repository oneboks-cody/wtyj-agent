"""Separate conversational language from the reviewed document locale."""
import re
import secrets
import os
from urllib.parse import quote
from shared import state_registry, mermaid_catalog

PREFIX = 'mermaid-language:'
MEDIA_TYPE = 'mermaid_document_language'
FLAG = 'mermaid_document_language_picker'


def options():
    return mermaid_catalog.get_catalog().get('document_languages', {})


def needs_choice(fields):
    return bool(options() and fields.get('chat_language') and fields['chat_language'] not in options()
                and fields.get('document_language') not in options())


def selected_button(message, flags):
    value = str(message.get('_zernio_interactive_id') or '')
    if not value.startswith(PREFIX):
        return None
    pending = flags.get(FLAG) or {}
    parts = value[len(PREFIX):].split(':')
    if (len(parts) != 2 or parts[0] != pending.get('token')
            or pending.get('account') != message.get('_zernio_account_id')
            or not pending.get('account') or parts[1] not in options()):
        return None
    return parts[1]


def apply_understanding(fields, flags, understood, message):
    """Only structured language understanding or a bound list reply selects a locale."""
    if understood.get('security_event', 'none') != 'none':
        return
    previous_chat = fields.get('chat_language')
    button_choice = selected_button(message, flags)
    chat = fields.get('chat_language') if button_choice and fields.get('chat_language') else understood.get('chat_language')
    if isinstance(chat, str) and re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,8})?', chat):
        fields['chat_language'] = chat
    choice = button_choice
    typed = understood.get('document_language')
    excerpt = understood.get('document_language_excerpt')
    if not choice and not str(message.get('_zernio_interactive_id') or '').startswith(PREFIX) and typed in options() and isinstance(excerpt, str) and excerpt.strip() and excerpt in str(message.get('text') or ''):
        choice = typed
    if choice:
        if previous_chat:
            fields['chat_language'] = previous_chat
        fields['document_language'] = choice
        fields['language'] = choice
        flags.pop(FLAG, None)
        # A changed document language requires showing the summary in that language again.
        if fields.get('phase') == 'awaiting_summary_confirmation':
            fields['phase'] = 'collecting'
    elif fields.get('document_language') in options():
        fields['language'] = fields['document_language']
    if needs_choice(fields):
        prompt = str(understood.get('document_language_prompt') or '').replace('\\n', '\n').strip()
        if prompt:
            previous = flags.get(FLAG) or {}
            flags[FLAG] = {'token': previous.get('token') if previous.get('source_message_id') == message.get('message_id') else secrets.token_hex(12),
                          'source_message_id': message.get('message_id'),
                          'account': str(message.get('_zernio_account_id') or ''),
                          'text': prompt[:900],
                          'button': str(understood.get('document_language_button') or '🌐')[:20]}


def attach(reply, message):
    state = state_registry.wa_get_booking_state(str(message.get('from') or ''))
    fields = (state.get('fields') or {}).get('mermaid_intake') or {}
    picker = (state.get('flags') or {}).get(FLAG) or {}
    if (needs_choice(fields) and picker.get('text') and not reply.get('media')
            and picker.get('source_message_id') == message.get('message_id')):
        return {**reply, 'text': picker['text'],
                'media': {'type': MEDIA_TYPE, 'url': picker['token']}}
    return reply


def send_picker(conversation_id, account_id, token):
    """One native list containing the six reviewed document languages."""
    from agents.social import zernio_dm_client as provider
    state = state_registry.wa_get_booking_state(conversation_id)
    fields = (state.get('fields') or {}).get('mermaid_intake') or {}
    picker = (state.get('flags') or {}).get(FLAG) or {}
    if not needs_choice(fields) or (picker.get('token'), picker.get('account')) != (token, account_id):
        return False
    if not provider._provider_mutation_account_allowed(account_id, 'mermaid_document_language'):
        return False
    endpoint = 'https://zernio.com/api/v1/inbox/conversations/' + quote(conversation_id, safe='')
    headers = {'Authorization': 'Bearer ' + os.environ.get('LATE_API_KEY', ''),
               'Content-Type': 'application/json', 'Idempotency-Key': PREFIX + token}
    window, _ = provider._recommendation_session_open(endpoint, headers, account_id)
    if not window:
        return False
    payload = {'accountId': account_id, 'interactive': {'type': 'list',
        'body': {'text': picker['text']}, 'action': {'button': picker['button'] or '🌐',
        'sections': [{'rows': [{'id': PREFIX + token + ':' + locale, 'title': title}
                              for locale, title in options().items()]}]}}}
    outcome, status, message_id = provider._post_recommendation_message(endpoint + '/messages', headers, payload)
    return bool(message_id and provider._confirm_recommendation_status(
        endpoint + '/messages', headers, account_id, message_id, require_delivered=True) == 'sent')
