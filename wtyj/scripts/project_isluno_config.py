"""C1 only: protected config projection. Transport is OFF unless separately approved."""
import argparse
import datetime
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys

# Import reviewed stdlib transport helpers only; never import application configuration.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_isluno_target import Rejected, require, safe_open, bounded_command, ssh_argv

CONFIG = '/root/clients/mermaid/config/client.json'
LIMIT = 1024 * 1024
FEATURES = ('mermaid_reservation_demo', 'mermaid_reminders',
            'isluno_itinerary_demo_v1', 'mermaid_cutover_coordination')


def signature(info):
    return tuple(getattr(info, key) for key in (
        'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
        'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def selected(document, parent, key, kind):
    if parent not in document:
        return 'missing'
    values = document[parent]
    if not isinstance(values, dict):
        return 'invalid'
    if key not in values:
        return 'missing'
    value = values[key]
    if kind == 'boolean':
        return value if type(value) is bool else 'invalid'
    if kind == 'generation':
        return value if type(value) is int and 1 <= value <= 2**63-1 else 'invalid'
    return bool(value.strip()) if isinstance(value, str) and len(value) <= 4096 else 'invalid'


def project(raw):
    require(0 < len(raw) <= LIMIT)
    doc = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                     parse_constant=lambda _: require(False))
    require(isinstance(doc, dict) and doc.get('slug') == 'mermaid')
    require(isinstance(doc.get('business'), dict) and doc['business'].get('slug') == 'mermaid')
    allow = doc.get('channel_account_allowlist')
    require(isinstance(allow, dict) and allow.get('mode') == 'strict')
    accounts = allow.get('zernio_accounts')
    require(isinstance(accounts, list) and len(accounts) == 1)
    account = accounts[0]
    require(isinstance(account, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', account))
    public = dict(slug='mermaid', business_slug='mermaid', allowlist_mode='strict',
                  account_count=1, account_fingerprint=hashlib.sha256(account.encode()).hexdigest(),
                  config_sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw),
                  features={key: selected(doc, 'features', key, 'boolean') for key in FEATURES},
                  activation_generation=selected(doc, 'mermaid_maintenance', 'activation_generation', 'generation'),
                  native_carousels=selected(doc, 'isluno', 'native_carousels', 'boolean'),
                  media_base_present=selected(doc, 'isluno', 'media_base_url', 'presence'),
                  document_base_present=selected(doc, 'isluno', 'document_base_url', 'presence'))
    return {'public': public, 'private_account_id': account}


def verify_path(path, original):
    fd = safe_open(path)
    try:
        require(signature(os.fstat(fd)) == original)
    finally:
        os.close(fd)


def snapshot(path=CONFIG):
    """Shared flock on EXISTING canonical sidecar. Never create/chmod/write remotely."""
    lock = safe_open(path + '.lock')
    config = None
    try:
        lock_sig = signature(os.fstat(lock))
        require(os.fstat(lock).st_nlink == 1)
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        verify_path(path + '.lock', lock_sig)
        config = safe_open(path)
        before = os.fstat(config)
        require(before.st_nlink == 1 and 0 < before.st_size <= LIMIT)
        sig = signature(before)
        raw = bytearray()
        while len(raw) < before.st_size:
            block = os.read(config, min(65536, before.st_size - len(raw)))
            require(bool(block))
            raw.extend(block)
        require(not os.read(config, 1))
        result = project(bytes(raw))
        require(signature(os.fstat(config)) == sig)
        verify_path(path, sig)
        verify_path(path + '.lock', lock_sig)
        require(signature(os.fstat(lock)) == lock_sig)
        return result
    finally:
        if config is not None:
            os.close(config)
        os.close(lock)


def remote_main():
    def expired(*_):
        raise Rejected('timeout')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(15)
    try:
        result = snapshot()
        result['observed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        print(json.dumps(result, sort_keys=True))
    except Exception:
        print('{"status":"stopped"}')
    finally:
        signal.alarm(0)


def payload():
    imports = 'import datetime,fcntl,hashlib,json,os,re,signal,stat\nfrom pathlib import PurePosixPath\n'
    constants = f'CONFIG={CONFIG!r}\nLIMIT={LIMIT!r}\nFEATURES={FEATURES!r}\n'
    functions = (Rejected, require, safe_open, signature, unique_object, selected,
                 project, verify_path, snapshot, remote_main)
    return (imports + constants + '\n'.join(inspect.getsource(fn) for fn in functions)
            + '\nremote_main()\n').encode()


def validate_result(raw):
    require(len(raw.encode()) <= 8192)
    result = json.loads(raw, object_pairs_hook=unique_object)
    require(isinstance(result, dict) and set(result) == {'public', 'private_account_id', 'observed_at'})
    public = result['public']
    require(isinstance(public, dict))
    account = result['private_account_id']
    # Reconstruct only the permitted schema, then compare every selected value.
    require(isinstance(account, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', account))
    require(set(public) == {'slug','business_slug','allowlist_mode','account_count','account_fingerprint',
            'config_sha256','byte_count','features','activation_generation','native_carousels',
            'media_base_present','document_base_present'})
    require(public['slug'] == public['business_slug'] == 'mermaid' and public['allowlist_mode'] == 'strict')
    require(type(public['account_count']) is int and public['account_count'] == 1)
    require(public['account_fingerprint'] == hashlib.sha256(account.encode()).hexdigest())
    require(isinstance(public['config_sha256'], str) and re.fullmatch('[0-9a-f]{64}', public['config_sha256']))
    require(type(public['byte_count']) is int and 0 < public['byte_count'] <= LIMIT)
    require(isinstance(public['features'], dict) and set(public['features']) == set(FEATURES))
    for value in list(public['features'].values()) + [public[k] for k in (
            'native_carousels', 'media_base_present', 'document_base_present')]:
        require(type(value) is bool or (type(value) is str and value in ('missing','invalid')))
    value = public['activation_generation']
    require((type(value) is int and 1 <= value <= 2**63-1) or
            (type(value) is str and value in ('missing','invalid')))
    stamp = result['observed_at']
    require(isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00', stamp))
    datetime.datetime.fromisoformat(stamp)
    return result


class FixedParser(argparse.ArgumentParser):
    def error(self, message):
        raise Rejected('invalid_arguments')


def main(argv=None):
    parser = FixedParser(description=__doc__)
    parser.add_argument('--execute-authorized-host', action='store_true')
    parser.add_argument('--authorization-reference')
    args = parser.parse_args(argv)
    if not args.execute_authorized_host:
        print('{"status":"offline_default","host_contacted":false}')
        return 0
    require(args.authorization_reference and re.fullmatch('[A-Za-z0-9_-]{1,100}', args.authorization_reference))
    # Fixed new private local directory, created BEFORE transport; never overwrite a prior run.
    directory = Path(__file__).resolve().parents[2] / 'tmp' / ('isluno-c1-' + args.authorization_reference)
    parent = safe_open(str(directory.parent), directory=True)
    child = None
    try:
        os.mkdir(directory.name, 0o700, dir_fd=parent)
        child = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        require(stat.S_IMODE(os.fstat(child).st_mode) == 0o700)
        result = validate_result(bounded_command(ssh_argv(), timeout=30, cap=8192, data=payload()))
        fd = os.open('binding.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=child)
        with os.fdopen(fd, 'w') as stream:
            require(stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600)
            json.dump(result, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps({'status':'observed', 'observed_at':result['observed_at'], 'projection':result['public']}))
        return 0
    finally:
        if child is not None:
            os.close(child)
        os.close(parent)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('{"status":"stopped","failed_check":"C1"}')
        raise SystemExit(1)
