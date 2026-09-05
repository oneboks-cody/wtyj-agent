#!/usr/bin/env python3
"""Interactive localhost setup for Mermaid's dedicated Google app password.

Run with --serve --sender hello@1boks.com --host root@108.61.192.52.
The password travels over SSH stdin, never shell arguments or local storage.
The remote check authenticates only: no email is sent and no inbox is read.
"""

from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import tempfile
from threading import Thread
from urllib.parse import parse_qs


RESULT_PATH = Path('/tmp/mermaid-gmail-connection-result.json')

REMOTE_SETUP = r'''
import json, os, secrets, smtplib, ssl, subprocess, sys
from pathlib import Path

def result(ok, code=''):
    print(json.dumps({'ok': bool(ok), 'code': code}), flush=True)

def verify_sender(sender):
    info = json.loads(subprocess.check_output(
        ['docker', 'inspect', 'wtyj-mermaid'], stderr=subprocess.DEVNULL, timeout=10))[0]
    env = dict(item.split('=', 1) for item in info['Config'].get('Env', []) if '=' in item)
    if env.get('MERMAID_EMAIL_ADDRESS', '').strip().casefold() != sender.casefold():
        return False
    if env.get('MERMAID_EMAIL_PASSWORD_FILE', '/app/config/mermaid_email_password') != '/app/config/mermaid_email_password':
        return False
    for line in Path('/root/clients/mermaid/config/platform.env').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() == 'MERMAID_EMAIL_ADDRESS':
            if value.strip().strip('\"').strip("'").casefold() != sender.casefold():
                return False
    return True

temporary = None
client = None
try:
    raw = sys.stdin.buffer.read(8193)
    if len(raw) > 8192:
        raise ValueError('invalid input')
    supplied = json.loads(raw)
    sender = supplied['sender']
    password = supplied['password']
    if not isinstance(sender, str) or not isinstance(password, str):
        raise ValueError('invalid input')
    if any(ord(c) < 33 or ord(c) > 126 for c in sender + password) or len(password) != 16:
        raise ValueError('invalid input')
    if not verify_sender(sender):
        result(False, 'sender_mismatch')
        sys.exit(0)
    client = smtplib.SMTP('smtp.gmail.com', 587, timeout=20)
    client.ehlo()
    client.starttls(context=ssl.create_default_context())
    client.ehlo()
    client.login(sender, password)
    try:
        client.quit()
    except Exception:
        pass
    try:
        client.close()
    except Exception:
        pass
    client = None
    if not verify_sender(sender):
        result(False, 'sender_mismatch')
        sys.exit(0)
    directory = Path('/root/clients/mermaid/config')
    target = directory / 'mermaid_email_password'
    temporary = directory / ('.mermaid_email_password.' + secrets.token_hex(12))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(password + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    temporary = None
    os.chmod(target, 0o600)
    result(True)
except smtplib.SMTPAuthenticationError:
    result(False, 'authentication_failed')
except Exception:
    result(False, 'connection_failed')
finally:
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    if temporary is not None:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass
'''


def _write_status(status: str, sender: str) -> None:
    descriptor, path = tempfile.mkstemp(prefix='.mermaid-gmail-result-', dir=str(RESULT_PATH.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'status': status, 'sender': sender}, stream)
        os.replace(path, RESULT_PATH)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _connect(host: str, sender: str, password: str) -> dict:
    command = 'python3 -c ' + shlex.quote(REMOTE_SETUP)
    try:
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '--', host, command],
            input=json.dumps({'sender': sender, 'password': password}),
            text=True, capture_output=True, timeout=85, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 2048:
            return {'ok': False, 'code': 'connection_failed'}
        data = json.loads(result.stdout)
        if data.get('ok') is True:
            return {'ok': True, 'code': ''}
        code = data.get('code')
        return {'ok': False, 'code': code if code in {
            'authentication_failed', 'sender_mismatch', 'connection_failed',
        } else 'connection_failed'}
    except (subprocess.SubprocessError, OSError, ValueError, TypeError):
        return {'ok': False, 'code': 'connection_failed'}


class SetupServer(HTTPServer):
    def __init__(self, sender: str, host: str):
        super().__init__(('127.0.0.1', 0), SetupHandler)
        self.sender = sender
        self.ssh_host = host
        self.secret_path = '/connect/' + secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.expected_host = f'127.0.0.1:{self.server_port}'
        self.origin = 'http://' + self.expected_host
        self.connected = False

    def handle_error(self, request, client_address):
        # Neither malformed requests nor request bodies belong in console logs.
        pass


class SetupHandler(BaseHTTPRequestHandler):
    server: SetupServer

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass

    def version_string(self):
        return 'Local setup'

    def _valid_request(self, *, posting=False):
        if self.headers.get_all('Host') != [self.server.expected_host]:
            return False
        origins = self.headers.get_all('Origin') or []
        if (posting and origins != [self.server.origin]) or (origins and origins != [self.server.origin]):
            return False
        if self.headers.get('Sec-Fetch-Site', 'none') not in {'none', 'same-origin'}:
            return False
        return self.path == self.server.secret_path

    def _respond(self, body: str, status=200, *, style_nonce=''):
        encoded = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'nonce-" + style_nonce + "'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def _page(self, error=''):
        nonce = secrets.token_urlsafe(24)
        sender = html.escape(self.server.sender, quote=True)
        if self.server.connected:
            contents = f'<div class="badge">Connected</div><h1>Tracy is ready to email.</h1><p><strong>{sender}</strong> is connected for reservation emails.</p><p>You can close this window. No email was sent during setup.</p>'
        else:
            alert = '<p class="error" role="alert">' + html.escape(error) + '</p>' if error else ''
            contents = f'''<div class="badge">Mermaid · Email setup</div><h1>A thoughtful final touch.</h1>
<p>Connect <strong>{sender}</strong> so Tracy can send guests their booking details and receipt when they ask.</p>
<ol><li>Open <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Google App passwords</a> while signed in to this mailbox.</li><li>Create an app password named <strong>Tracy reservations</strong>.</li><li>Paste its 16 characters below.</li></ol>
<p class="hint">Use an app password, not your normal Google password. If Google does not show app passwords, check that 2-Step Verification is enabled and that your Workspace administrator allows them.</p>
{alert}<form method="post" action="{html.escape(self.server.secret_path, quote=True)}" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(self.server.csrf, quote=True)}">
<label for="password">Google app password</label><input id="password" name="password" type="password" autocomplete="off" spellcheck="false" autocapitalize="none" required maxlength="64" placeholder="xxxx xxxx xxxx xxxx">
<button type="submit">Connect reservation email</button></form>
<p class="hint">This local form securely verifies the mailbox through SSH and stores the app password only on the Mermaid server. It does not send an email or read the inbox.</p>'''
        self._respond(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="same-origin"><title>Connect Mermaid email</title>
<style nonce="{nonce}">*{{box-sizing:border-box}}body{{margin:0;padding:44px 18px;background:linear-gradient(145deg,#e5f7fa,#f7f3e9);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#214652}}main{{max-width:600px;margin:auto;padding:38px;background:white;border-radius:20px;border-top:6px solid #087e99;box-shadow:0 20px 70px #12485415}}.badge{{font-size:12px;text-transform:uppercase;letter-spacing:1.6px;color:#007a91;font-weight:700}}h1{{font-size:32px;line-height:1.2;letter-spacing:-.7px;color:#083e54;margin:18px 0}}p{{margin:16px 0}}ol{{padding-left:23px}}li{{margin:10px 0}}a{{color:#007991}}.hint{{font-size:13px;color:#58727c}}label{{display:block;margin:24px 0 8px;font-weight:600}}input[type=password]{{display:block;width:100%;padding:15px;border:1px solid #b4cbd1;border-radius:8px;font:18px monospace;outline-color:#00869d}}button{{margin-top:15px;width:100%;border:0;border-radius:8px;padding:16px;color:white;background:#007d96;font:600 16px inherit;cursor:pointer}}.error{{padding:12px 15px;border-radius:8px;background:#fff1e8;color:#8c3918}}@media(max-width:500px){{body{{padding:20px 12px}}main{{padding:26px 22px}}h1{{font-size:28px}}}}</style></head><body><main>{contents}</main></body></html>''', style_nonce=nonce)

    def do_GET(self):
        if not self._valid_request():
            self._respond('Not found.', 404)
            return
        self._page()

    def do_POST(self):
        if not self._valid_request(posting=True) or self.server.connected:
            self._respond('Request not accepted.', 403)
            return
        lengths = self.headers.get_all('Content-Length') or []
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or len(lengths[0]) > 4 or not lengths[0].isdigit():
            self._respond('Request not accepted.', 400)
            return
        length = int(lengths[0])
        if not 1 <= length <= 4096 or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/x-www-form-urlencoded':
            self._respond('Request not accepted.', 400)
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Incomplete request')
            fields = parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=4)
            if set(fields) != {'csrf', 'password'} or any(len(values) != 1 for values in fields.values()):
                raise ValueError('Invalid form')
            if not secrets.compare_digest(fields['csrf'][0], self.server.csrf):
                raise ValueError('Invalid token')
            password = fields['password'][0].strip().replace(' ', '')
            if len(password) != 16 or not re.fullmatch(r'[A-Za-z0-9]{16}', password):
                self._page('Please paste the 16-character Google app password. Spaces between groups are fine.')
                return
            outcome = _connect(self.server.ssh_host, self.server.sender, password)
        except (ValueError, TypeError, UnicodeError, TimeoutError, OSError):
            self._respond('Request not accepted.', 400)
            return
        finally:
            password = None
            fields = None
            body = None
        if outcome['ok']:
            self.server.connected = True
            _write_status('connected', self.server.sender)
            self._page()
            print(json.dumps({'connected': True, 'sender': self.server.sender}), flush=True)
            Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._page({
                'authentication_failed': 'Google did not accept that app password. Check the mailbox and app password, then try again.',
                'sender_mismatch': 'The Mermaid server is not configured for this sender yet. The app password was not saved.',
                'connection_failed': 'The secure connection could not be confirmed. Please try again.',
            }[outcome['code']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='Open an interactive localhost form; no credentials in arguments.')
    parser.add_argument('--sender', required=True)
    parser.add_argument('--host', required=True)
    args = parser.parse_args()
    if not args.serve:
        parser.error('--serve is required; a person must enter the app password in the local form')
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', args.sender) or not args.sender.isascii():
        parser.error('Provide one valid sender address')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', args.host):
        parser.error('Provide a valid SSH host')
    server = SetupServer(args.sender, args.host)
    _write_status('waiting', args.sender)
    print(server.origin + server.secret_path, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        _write_status('cancelled', args.sender)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
