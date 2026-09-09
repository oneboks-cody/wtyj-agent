"""One separately authorized tenant-profile GET. Default OFF; no credential discovery."""
import argparse
import datetime
import http.client
import json
import os
import re
import signal
import ssl
import stat
import sys
import time

HOST = 'api.unboks.org'
PATH = '/api/mermaid/dashboard/api/client/profile'
LIMIT = 8192
SECONDS = 10


class Stopped(Exception):
    pass


def require(condition):
    if not condition:
        raise Stopped()


class BudgetReader:
    """Count raw HTTP headers, framing and body BEFORE buffering beyond 8 KiB."""
    def __init__(self, stream):
        self.stream = stream
        self.used = 0

    def take(self, method, size=-1):
        remaining = LIMIT - self.used
        require(remaining > 0 or size == 0)
        if method == 'read' and size >= 0:
            require(size <= remaining)
        requested = remaining if size < 0 else min(size, remaining)
        data = getattr(self.stream, method)(requested)
        if method == 'read':
            while len(data) < requested:
                block = self.stream.read(requested-len(data))
                if not block:
                    break
                data += block
        self.used += len(data)
        require(self.used <= LIMIT)
        return data

    def read(self, size=-1):
        return self.take('read', size)

    def readline(self, size=-1):
        return self.take('readline', size)

    def flush(self):
        pass  # Read-only stream; required by HTTPResponse.close on Python 3.14.

    def close(self):
        self.stream.close()


class BudgetSocket:
    def __init__(self, sock):
        self.sock = sock

    def makefile(self, *args, **kwargs):
        # Unbuffered transport: only the capped reader may buffer response bytes.
        return BudgetReader(self.sock.makefile('rb', buffering=0))


def response_factory(sock, **kwargs):
    return http.client.HTTPResponse(BudgetSocket(sock), **kwargs)


def credential_from_stdin():
    # Pipes/socket adapters only. Never open a credential file or read an env var.
    mode = os.fstat(0).st_mode
    require(stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode))
    raw = bytearray()
    while True:
        chunk = os.read(0, min(1024, 4097-len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
        require(len(raw) <= 4096)
    token = bytes(raw).decode('ascii')
    # RFC6750 b64token grammar; forbid whitespace/header injection, including LF.
    require(re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token) is not None)
    return token


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def check(token, deadline):
    require(isinstance(token, str) and len(token) <= 4096 and
            re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token) is not None)
    context = ssl.create_default_context()
    require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED)
    left = deadline-time.monotonic()
    require(left > 0)
    connection = http.client.HTTPSConnection(HOST, 443, timeout=left, context=context)
    connection.response_class = response_factory
    response = None
    try:
        # http.client does not follow redirects, refresh authentication or retry.
        connection.request('GET', PATH, headers={'Authorization':'Bearer '+token,
                           'Accept':'application/json', 'Connection':'close'})
        response = connection.getresponse()
        require(response.status == 200)
        require(len(response.headers.get_all('Content-Length', [])) <= 1)
        require(len(response.headers.get_all('Transfer-Encoding', [])) <= 1)
        require(not (response.getheader('Content-Length') and response.getheader('Transfer-Encoding')))
        require(response.getheader('Transfer-Encoding', '').lower() in ('', 'chunked'))
        require(response.getheader('Content-Type', '').split(';',1)[0].strip().lower() == 'application/json')
        require(response.getheader('Content-Encoding', 'identity').lower() == 'identity')
        body = response.read(LIMIT+1)
        require(len(body) <= LIMIT and time.monotonic() < deadline)
        doc = json.loads(body.decode('utf-8'), object_pairs_hook=unique_object,
                         parse_constant=lambda _: require(False))
        require(isinstance(doc, dict) and type(doc.get('slug')) is str and doc['slug'] == 'mermaid')
        # Known display fields may be present but never leave this process.
        require(set(doc) <= {'slug','name','business_name','display_name','status','business'})
        for key in ('name','business_name','display_name','status'):
            if key in doc:
                require(isinstance(doc[key],str) and len(doc[key]) <= 2048)
        if 'business' in doc:
            require(isinstance(doc['business'],dict) and set(doc['business']) <= {'name','display_name'})
            require(all(isinstance(v,str) and len(v) <= 2048 for v in doc['business'].values()))
        return {'status':'observed', 'http_status':200, 'tenant_matches':True,
                'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


class FixedParser(argparse.ArgumentParser):
    def error(self, message):
        raise Stopped()


def main(argv=None):
    try:
        parser = FixedParser(description=__doc__)
        parser.add_argument('--execute-authorized-request', action='store_true')
        parser.add_argument('--authorization-reference')
        args = parser.parse_args(argv)
        if not args.execute_authorized_request:
            print('{"status":"offline_default","host_contacted":false}')
            return 0
        require(args.authorization_reference == 'calvin-dashboard-tenant-once')
        def expired(*_):
            raise Stopped()
        previous = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, SECONDS)
        deadline = time.monotonic()+SECONDS
        try:
            result = check(credential_from_stdin(), deadline)
            require(time.monotonic() < deadline)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print('{"status":"stopped","check":"dashboard_tenant"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
