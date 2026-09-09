"""Bounded H1-H7 metadata inspection. Default is offline; execution needs separate authority.

No application imports, protected config reads, DB queries or provider calls.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

HOST = '108.61.192.52'
CONTAINER = 'wtyj-mermaid'
ROOT = '/root/clients/mermaid'
POINTER = '/var/www/unboks-dashboard/current'
RELEASES = '/var/www/unboks-dashboard/releases'
MAX_OUTPUT = 65536
FILE_LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')


class Rejected(Exception):
    """Only fixed reason codes may cross the output boundary."""


def require(ok, reason='invalid_metadata'):
    if not ok:
        raise Rejected(reason)


def bounded_command(argv, *, timeout=15, cap=MAX_OUTPUT, data=None):
    """Bound stdout+stderr together before buffering; never expose stderr."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    sel = selectors.DefaultSelector()
    result = bytearray()
    count = 0
    end = time.monotonic() + timeout
    pending = memoryview(data or b'')
    try:
        for stream in (proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
            sel.register(stream, selectors.EVENT_READ)
        if proc.stdin:
            os.set_blocking(proc.stdin.fileno(), False)
            if pending:
                sel.register(proc.stdin, selectors.EVENT_WRITE)
            else:
                proc.stdin.close()
        while sel.get_map():
            left = end - time.monotonic()
            require(left > 0, 'timeout')
            for key, event in sel.select(min(left, .1)):
                if event & selectors.EVENT_WRITE:
                    n = os.write(key.fd, pending[:8192]); pending = pending[n:]
                    if not pending:
                        sel.unregister(key.fileobj); key.fileobj.close()
                    continue
                block = os.read(key.fd, 8192)
                if not block:
                    sel.unregister(key.fileobj)
                    continue
                count += len(block)
                require(count <= cap, 'output_limit')
                if key.fileobj is proc.stdout:
                    result.extend(block)
        require(time.monotonic() < end, 'timeout')
        try:
            code = proc.wait(timeout=max(.001, end-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise Rejected('timeout') from None
        require(code == 0, 'command_failed')
        try:
            return result.decode('utf-8', errors='strict')
        except UnicodeError:
            raise Rejected('invalid_encoding') from None
    finally:
        sel.close()
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream and not stream.closed:
                stream.close()


def fields(text, count):
    require(text.count('\n') <= 1 and '\r' not in text)
    parts = text.strip().split('|')
    require(len(parts) == count and all(parts))
    return parts


def container_metadata(text):
    ident, name, image, running, started, service = fields(text, 6)
    require(HEX.fullmatch(ident) and name == '/'+CONTAINER and DIGEST.fullmatch(image)
            and running == 'true' and service == 'agent', 'wrong_identity')
    require(re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z', started))
    return dict(id=ident, name=name, image=image, running=True, started_at=started, service=service)


def mount_metadata(text):
    lines = text.splitlines()
    require(len(lines) == 3, 'mount_validation_failed')
    seen = set(); safe = []
    for line in lines:
        parts = line.split('|')
        require(len(parts) == 4, 'mount_validation_failed')
        kind, source, destination, rw = parts
        suffix = destination.removeprefix('/app/')
        require(suffix in ('config', 'data', 'logs') and destination == '/app/'+suffix
                and destination not in seen and source == ROOT+'/'+suffix
                and kind == 'bind' and rw == 'true', 'mount_validation_failed')
        seen.add(destination)
        if suffix != 'logs':
            safe.append(dict(type=kind, source=source, destination=destination, writable=True))
    return dict(mounts=sorted(safe,key=lambda item:item["destination"]), expected_logs_mount_verified=True)


def safe_open(path, directory=False):
    """Walk every component with O_NOFOLLOW and dirfds, including parent dirs."""
    path = PurePosixPath(path)
    require(path.is_absolute() and '..' not in path.parts, 'unsafe_path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            is_dir = index < len(path.parts)-2 or directory
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if is_dir:
                flags |= os.O_DIRECTORY
            new = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = new
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'nonregular_path')
        return fd
    except BaseException:
        os.close(fd)
        raise


def path_metadata(path, directory=False):
    parsed = PurePosixPath(path)
    parent = safe_open(str(parsed.parent), True)
    try:
        info = os.stat(parsed.name, dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'nonregular_path')
        return dict(path=path, type='directory' if directory else 'file', bytes=info.st_size,
                    uid=info.st_uid, gid=info.st_gid, mode=oct(stat.S_IMODE(info.st_mode)))
    finally:
        os.close(parent)


def file_hash(path, budget, file_limit=FILE_LIMIT, total_limit=TOTAL_LIMIT):
    fd = safe_open(path)
    try:
        before = os.fstat(fd)
        require(before.st_size <= file_limit, 'file_limit')
        require(budget[0]+before.st_size <= total_limit, 'read_limit')
        size = 0; h = hashlib.sha256()
        while size < before.st_size:
            block = os.read(fd, min(65536, before.st_size-size))
            require(bool(block), 'file_changed')
            size += len(block); budget[0] += len(block)
            require(size <= file_limit, 'file_limit')
            require(budget[0] <= total_limit, 'read_limit')
            h.update(block)
        after = os.fstat(fd)
        require((before.st_size, before.st_mtime_ns, before.st_ino) ==
                (after.st_size, after.st_mtime_ns, after.st_ino) and size == before.st_size, 'file_changed')
        return h.hexdigest()
    finally:
        os.close(fd)


def source_paths(paths):
    require(isinstance(paths, list) and len(paths) == 112 and len(set(paths)) == 112, 'invalid_manifest')
    for path in paths:
        require(isinstance(path, str) and re.fullmatch(r'[A-Za-z0-9_/-]+\.py', path)
                and not path.startswith('/') and '..' not in path.split('/')
                and not set(path.split('/')) & {'config', 'data', 'logs', 'tmp', 'tests'}, 'invalid_manifest')
    return paths


def alarm_handler(*_):
    raise Rejected('timeout')


def run_checks(paths, run=bounded_command):
    results = {}; current = 'H1'; end = time.monotonic()+120
    def cmd(argv, data=None):
        remaining = end-time.monotonic(); require(remaining > 0, 'timeout')
        return run(argv, timeout=min(15, remaining), cap=MAX_OUTPUT, data=data)
    def inspect(fmt):
        return cmd(['docker', 'inspect', '--format', fmt, CONTAINER])
    try:
        # The alarm bounds filesystem reads as well as child commands.
        signal.signal(signal.SIGALRM, alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, 15)
        os_name = cmd(['uname', '-s']).strip(); arch = cmd(['uname', '-m']).strip()
        require(os_name == 'Linux' and arch in ('x86_64', 'aarch64'), 'unsupported_platform')
        results[current] = dict(os=os_name, architecture=arch)
        current = 'H2'; signal.setitimer(signal.ITIMER_REAL, min(15,end-time.monotonic()))
        c = container_metadata(inspect('{{.Id}}|{{.Name}}|{{.Image}}|{{.State.Running}}|{{.State.StartedAt}}|{{index .Config.Labels "com.docker.compose.service"}}'))
        results[current] = c
        current = 'H3'; signal.setitimer(signal.ITIMER_REAL, min(15,end-time.monotonic()))
        image, ios, ia = fields(cmd(['docker','image','inspect','--format','{{.Id}}|{{.Os}}|{{.Architecture}}',c['image']]),3)
        require(image == c['image'] and ios == 'linux' and ia == {'x86_64':'amd64','aarch64':'arm64'}[arch], 'wrong_image')
        results[current] = dict(id=image, os=ios, architecture=ia)
        current = 'H4'; signal.setitimer(signal.ITIMER_REAL, min(15,end-time.monotonic()))
        results[current] = mount_metadata(inspect('{{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}}|{{.RW}}{{println}}{{end}}'))
        current = 'H5'; signal.setitimer(signal.ITIMER_REAL, min(15,end-time.monotonic()))
        results[current] = [path_metadata(ROOT+'/docker-compose.yml'), path_metadata(ROOT+'/config',True),
                            path_metadata(ROOT+'/data',True), path_metadata(ROOT+'/data/state_registry.db')]
        current = 'H6'; signal.setitimer(signal.ITIMER_REAL, min(15,end-time.monotonic()))
        # Resolve just one expected symlink, with a no-follow parent descriptor.
        parent = safe_open(str(PurePosixPath(POINTER).parent),True)
        try:
            target = os.readlink('current',dir_fd=parent)
        finally:
            os.close(parent)
        if not target.startswith('/'):
            target = str(PurePosixPath(POINTER).parent / target)
        require(re.fullmatch(re.escape(RELEASES)+r'/[0-9a-f]{40}',target), 'unsafe_pointer')
        budget = [0]; hashes = []
        for name in ('index.html','assets/index-LhrQCX2T.js','assets/index-C6GIBlmS.css'):
            hashes.append(dict(path=name, sha256=file_hash(target+'/'+name,budget)))
        results[current] = dict(pointer_type='symlink',target=target,files=hashes)
        current = 'H7'; signal.setitimer(signal.ITIMER_REAL, min(15,end-time.monotonic()))
        source_paths(paths)
        # Exact hash-only subset of this reviewed file; no application imports.
        script = HASH_WORKER + '\npaths = '+repr(paths)+'''\nbudget=[0]\nprint(json.dumps([dict(path=p,sha256=file_hash('/app/'+p,budget)) for p in paths]))\n'''
        raw = cmd(['docker','exec','-i',CONTAINER,'python','-I','-B','-'], data=script.encode())
        value = json.loads(raw)
        require(isinstance(value,list) and len(value)==len(paths), 'invalid_source_result')
        for expected, item in zip(paths,value):
            require(isinstance(item,dict) and set(item)=={'path','sha256'} and item['path']==expected
                    and isinstance(item['sha256'],str) and HEX.fullmatch(item['sha256']), 'invalid_source_result')
        results[current] = value
        return dict(status='observed',checks=results)
    except Exception:
        # Never echo exception text, stdout, stderr, unexpected paths or partial checks.
        return dict(status='stopped',failed_check=current,checks={})
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)


# Construct only stdlib definitions for the container hash process, with its own timer.
import inspect
HASH_WORKER = 'import os,stat,hashlib,json,signal\nfrom pathlib import PurePosixPath\n' + \
    f'FILE_LIMIT={FILE_LIMIT}\nTOTAL_LIMIT={TOTAL_LIMIT}\n' + '\n'.join(inspect.getsource(x) for x in
    (Rejected,require,safe_open,file_hash,alarm_handler)) + \
    '\nsignal.signal(signal.SIGALRM,alarm_handler)\nsignal.alarm(15)\n'


def ssh_argv():
    return ['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1',
            '-o','ConnectTimeout=10','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1',
            'root@'+HOST,'python3','-I','-B','-']


def remote_payload(paths):
    source_paths(paths)
    source = Path(__file__).read_text()
    source = source[:source.rindex("\nif __name__ == '__main__':")]
    a = source.index('import inspect\n'); b = source.index('\n\ndef ssh_argv()',a)
    source = source[:a]+'HASH_WORKER = '+repr(HASH_WORKER)+source[b:]
    return (source+'\nprint(json.dumps(run_checks('+repr(paths)+')))\n').encode()


def validate_result(value, paths):
    require(isinstance(value,dict), 'invalid_result')
    if value.get('status') == 'stopped':
        require(set(value)=={'status','checks','failed_check'} and value['checks']=={}
                and value['failed_check'] in {'H'+str(i) for i in range(1,8)},'invalid_result')
        return value
    require(set(value)=={'status','checks'} and value['status']=='observed','invalid_result')
    checks=value['checks']; require(isinstance(checks,dict) and set(checks)=={'H'+str(i) for i in range(1,8)},'invalid_result')
    h=checks['H1']; require(set(h)=={'os','architecture'} and h['os']=='Linux' and h['architecture'] in ('x86_64','aarch64'))
    h=checks['H2']; require(set(h)=={'id','name','image','running','started_at','service'} and h['running'] is True)
    container_metadata('|'.join([h['id'],h['name'],h['image'],'true',h['started_at'],h['service']]))
    h=checks['H3']; require(set(h)=={'id','os','architecture'} and h['id']==checks['H2']['image'] and h['os']=='linux'
                            and h['architecture']=={'x86_64':'amd64','aarch64':'arm64'}[checks['H1']['architecture']])
    h=checks['H4']; require(set(h)=={'mounts','expected_logs_mount_verified'} and h['expected_logs_mount_verified'] is True)
    expected=mount_metadata('\n'.join('bind|'+ROOT+'/'+n+'|/app/'+n+'|true' for n in ('config','data','logs')))
    require(h==expected)
    h=checks['H5']; require(isinstance(h,list) and len(h)==4)
    for item,(path,kind) in zip(h,[(ROOT+'/docker-compose.yml','file'),(ROOT+'/config','directory'),(ROOT+'/data','directory'),(ROOT+'/data/state_registry.db','file')]):
        require(set(item)=={'path','type','bytes','uid','gid','mode'} and item['path']==path and item['type']==kind)
        require(all(type(item[k]) is int and 0 <= item[k] < 2**63 for k in ('bytes','uid','gid')) and re.fullmatch(r'0o[0-7]{1,4}',item['mode']))
    h=checks['H6']; require(set(h)=={'pointer_type','target','files'} and h['pointer_type']=='symlink'
                           and re.fullmatch(re.escape(RELEASES)+r'/[0-9a-f]{40}',h['target']))
    for rows,names in [(h['files'],['index.html','assets/index-LhrQCX2T.js','assets/index-C6GIBlmS.css']),(checks['H7'],paths)]:
        require(isinstance(rows,list) and len(rows)==len(names))
        for row,name in zip(rows,names):
            require(set(row)=={'path','sha256'} and row['path']==name and HEX.fullmatch(row['sha256']))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized-host',action='store_true')
    parser.add_argument('--authorization-reference')
    args = parser.parse_args()
    if not args.execute_authorized_host:
        print(json.dumps({'status':'offline_default','host_contacted':False}))
        return 0
    # An operator reference is an audit field, never a substitute for Calvin's approval.
    require(args.authorization_reference and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',args.authorization_reference),'authorization_reference_required')
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root/'wtyj/briefs/isluno_baseline_manifest.json').read_text())
    paths = source_paths([x['path'] for x in manifest['backend']['runtime']['python_files']])
    payload = remote_payload(paths)
    try:
        raw=bounded_command(ssh_argv(),timeout=120,cap=MAX_OUTPUT,data=payload)
        result=validate_result(json.loads(raw),paths)
        result.update(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      authorization_reference=args.authorization_reference)
        encoded=json.dumps(result); require(len(encoded.encode())<=MAX_OUTPUT,'output_limit')
        print(encoded)
        return 0 if result['status']=='observed' else 1
    except Exception:
        print(json.dumps({'status':'stopped','failed_check':'transport','checks':{}}))
        return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({'status':'stopped','failed_check':'local_preflight','checks':{}}))
        raise SystemExit(1)
