"""C1 synthetic files only. No host, secrets, application imports or provider calls."""
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/project_isluno_config.py'
spec = importlib.util.spec_from_file_location('c1', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DOC = {'slug':'mermaid', 'business':{'slug':'mermaid'},
       'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic-account']},
       'features':{'mermaid_cutover_coordination':False},
       'mermaid_maintenance':{'activation_generation':7},
       'isluno':{'native_carousels':True, 'media_base_url':'https://example.invalid/?secret=CANARY',
                 'document_base_url':''}, 'secret':'CANARY'}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name).resolve() / 'client.json')
        self.raw = json.dumps(DOC).encode()
        Path(self.path).write_bytes(self.raw)
        Path(self.path + '.lock').touch(mode=0o600)
        self.guard = patch.object(m, 'bounded_command', side_effect=AssertionError('transport disabled'))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def test_same_bytes_and_selected_only(self):
        result = m.snapshot(self.path)
        self.assertEqual(result['private_account_id'], 'synthetic-account')
        public = result['public']
        self.assertEqual(public['config_sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(public['byte_count'], len(self.raw))
        self.assertTrue(public['media_base_present']); self.assertFalse(public['document_base_present'])
        self.assertEqual(public['activation_generation'], 7)
        self.assertNotIn('CANARY', json.dumps(result))
        self.assertNotIn('synthetic-account', json.dumps(public))
        result['observed_at'] = '2026-09-09T00:00:00.000001+00:00'
        self.assertEqual(m.validate_result(json.dumps(result)), result)

    def test_identity_duplicates_and_malformed(self):
        variants = [b'{secret CANARY', b'[]', b'{"slug":"mermaid","slug":"mermaid"}', b'\xff']
        for change in ({'slug':'other'}, {'business':{'slug':'other'}},
                       {'channel_account_allowlist':{'mode':'permissive','zernio_accounts':['x']}},
                       {'channel_account_allowlist':{'mode':'strict','zernio_accounts':[]}},
                       {'channel_account_allowlist':{'mode':'strict','zernio_accounts':['x','y']}},
                       {'channel_account_allowlist':{'mode':'strict','zernio_accounts':['secret\ncanary']}}):
            variants.append(json.dumps(dict(DOC, **change)).encode())
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(Exception): m.project(raw)
        with self.assertRaises(Exception): m.project(self.raw[:-1] + b', "n":NaN}')

    def test_fixed_states_and_generation_bounds(self):
        for value in (None, {}, [], 'CANARY', 0, 1, True):
            doc = copy.deepcopy(DOC); doc['features'] = {'mermaid_cutover_coordination':value}
            state = m.project(json.dumps(doc).encode())['public']['features']['mermaid_cutover_coordination']
            self.assertEqual(state, value if type(value) is bool else 'invalid')
        for value in (0, -1, 2**63, True, '1', {}, None):
            doc = copy.deepcopy(DOC); doc['mermaid_maintenance']['activation_generation'] = value
            self.assertEqual(m.project(json.dumps(doc).encode())['public']['activation_generation'], 'invalid')
        doc = copy.deepcopy(DOC); doc['isluno'] = 'CANARY'; doc.pop('features')
        public = m.project(json.dumps(doc).encode())['public']
        self.assertEqual(public['media_base_present'], 'invalid')
        self.assertTrue(all(x == 'missing' for x in public['features'].values()))

    def test_missing_and_busy_lock_no_creation_or_wait(self):
        os.unlink(self.path + '.lock')
        with self.assertRaises(Exception): m.snapshot(self.path)
        self.assertFalse(Path(self.path + '.lock').exists())
        Path(self.path + '.lock').touch()
        with open(self.path + '.lock', 'rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): m.snapshot(self.path)

    def test_symlink_fifo_directory_hardlink_and_escape(self):
        original = Path(self.path).with_name('original'); os.rename(self.path, original)
        os.symlink(original, self.path)
        with self.assertRaises(Exception): m.snapshot(self.path)
        os.unlink(self.path); os.mkfifo(self.path)
        with self.assertRaises(Exception): m.snapshot(self.path)
        os.unlink(self.path); os.mkdir(self.path)
        with self.assertRaises(Exception): m.snapshot(self.path)
        os.rmdir(self.path); os.link(original, self.path)
        with self.assertRaises(Exception): m.snapshot(self.path)
        with self.assertRaises(Exception): m.snapshot(str(Path(self.temp.name) / '..' / 'client.json'))
        link = Path(self.temp.name) / 'parent'; link.symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaises(Exception): m.snapshot(str(link / 'client.json'))
        os.unlink(self.path); os.rename(original, self.path)
        os.unlink(self.path + '.lock'); os.symlink(self.path, self.path + '.lock')
        with self.assertRaises(Exception): m.snapshot(self.path)

    def test_size_cap_and_growth(self):
        with open(self.path, 'wb') as stream: stream.truncate(m.LIMIT+1)
        with patch.object(m.os, 'read', side_effect=AssertionError('oversize must not read')):
            with self.assertRaises(m.Rejected): m.snapshot(self.path)
        Path(self.path).write_bytes(self.raw)
        original = m.os.read; changed = False
        def grow(fd, count):
            nonlocal changed
            block = original(fd, count)
            if not changed:
                changed = True
                with open(self.path, 'ab') as stream: stream.write(b' ')
            return block
        with patch.object(m.os, 'read', side_effect=grow):
            with self.assertRaises(m.Rejected): m.snapshot(self.path)

    def test_config_lock_and_parent_replacement_rejected(self):
        for which in ('config', 'lock', 'parent'):
            with self.subTest(which=which), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp).resolve() / 'config'; directory.mkdir()
                path = directory / 'client.json'; path.write_bytes(self.raw)
                Path(str(path)+'.lock').touch()
                original = m.project
                def replace(raw):
                    value = original(raw)
                    if which == 'parent':
                        directory.rename(directory.with_name('old')); directory.mkdir()
                        path.write_bytes(raw); Path(str(path)+'.lock').touch()
                    else:
                        target = path if which == 'config' else Path(str(path)+'.lock')
                        other = target.with_name('new'); other.write_bytes(raw if which == 'config' else b'')
                        os.replace(other, target)
                    return value
                with patch.object(m, 'project', side_effect=replace):
                    with self.assertRaises(m.Rejected): m.snapshot(str(path))

    def test_inplace_edit_rejected(self):
        original = m.project
        def edit(raw):
            result = original(raw)
            Path(self.path).write_bytes(raw.replace(b'CANARY', b'EDITED'))
            return result
        with patch.object(m, 'project', side_effect=edit):
            with self.assertRaises(m.Rejected): m.snapshot(self.path)

    def test_result_boundary_rejects_unknown_or_wrong_fields(self):
        result = m.snapshot(self.path); result['observed_at'] = '2026-09-09T00:00:00.000001+00:00'
        for key, value in [('features', {'secret':'CANARY'}), ('activation_generation',True),
                           ('account_fingerprint','wrong'), ('media_base_present', {'secret':'CANARY'}),
                           ('account_count',True), ('config_sha256','CANARY')]:
            bad = copy.deepcopy(result); bad['public'][key] = value
            with self.assertRaises(Exception): m.validate_result(json.dumps(bad))
        bad = copy.deepcopy(result); bad['extra']='CANARY'
        with self.assertRaises(Exception): m.validate_result(json.dumps(bad))
        with self.assertRaises(Exception): m.validate_result('CANARY'*2000)

    def test_default_and_missing_authority_do_not_dispatch(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['host_contacted'], False)
        with self.assertRaises(m.Rejected): m.main(['--execute-authorized-host'])

    def test_remote_payload_is_standalone_and_fixed_failure(self):
        source = m.payload().decode()
        self.assertNotIn('config_loader', source)
        scope = {}; exec(compile(source.rsplit('\nremote_main()',1)[0], 'synthetic', 'exec'), scope)
        self.assertEqual(scope['CONFIG'], m.CONFIG)
        scope['CONFIG'] = self.path
        # Default parameter was fixed at definition; override only in synthetic fixture.
        scope['snapshot'] = lambda: m.snapshot(self.path)
        with contextlib.redirect_stdout(io.StringIO()) as out: scope['remote_main']()
        m.validate_result(out.getvalue())
        scope['snapshot'] = lambda: (_ for _ in ()).throw(ValueError('CANARY'))
        with contextlib.redirect_stdout(io.StringIO()) as out: scope['remote_main']()
        self.assertEqual(json.loads(out.getvalue()), {'status':'stopped'})

    def test_alarm_failure_and_invalid_arguments_are_fixed(self):
        scope = {}; exec(compile(m.payload().decode().rsplit('\nremote_main()',1)[0], 'synthetic', 'exec'), scope)
        handlers = {}
        def install(sig, handler): handlers[sig] = handler
        def expired(): handlers[m.signal.SIGALRM](None, None)
        scope['snapshot'] = expired
        with patch.object(m.signal, 'signal', side_effect=install), patch.object(m.signal, 'alarm') as alarm:
            with contextlib.redirect_stdout(io.StringIO()) as out: scope['remote_main']()
        self.assertEqual(json.loads(out.getvalue()), {'status':'stopped'})
        self.assertEqual(alarm.call_args_list[0].args, (15,))
        self.assertEqual(alarm.call_args_list[-1].args, (0,))
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(m.Rejected): m.main(['--CANARY'])
        self.assertEqual(errors.getvalue(), '')

    def test_private_binding_permissions_no_overwrite_and_public_output(self):
        # Relocate ONLY the local synthetic output root. No real config read.
        fake = Path(self.temp.name) / 'wtyj/scripts/project.py'; fake.parent.mkdir(parents=True)
        (Path(self.temp.name) / 'tmp').mkdir()
        result = m.snapshot(self.path); result['observed_at'] = '2026-09-09T00:00:00.000001+00:00'
        with patch.object(m, '__file__', str(fake)), patch.object(m, 'bounded_command', return_value=json.dumps(result)) as dispatch:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(m.main(['--execute-authorized-host','--authorization-reference','fixture']),0)
            self.assertNotIn('synthetic-account', out.getvalue())
            directory = Path(self.temp.name) / 'tmp/isluno-c1-fixture'
            self.assertEqual(directory.stat().st_mode & 0o777,0o700)
            self.assertEqual((directory/'binding.json').stat().st_mode & 0o777,0o600)
            self.assertEqual(json.loads((directory/'binding.json').read_text()),result)
            with self.assertRaises(FileExistsError): m.main(['--execute-authorized-host','--authorization-reference','fixture'])
            self.assertEqual(dispatch.call_count,1)
            self.assertEqual(dispatch.call_args.kwargs['timeout'],30)
            self.assertEqual(dispatch.call_args.kwargs['cap'],8192)


if __name__ == '__main__': unittest.main()
