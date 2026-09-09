"""Offline archive-recovery decisions and bounded command receipts; no SSH."""
import contextlib,hashlib,importlib.util,io,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
SCRIPT=Path(__file__).resolve().parents[2]/'scripts/recover_isluno_archive_staging.py'
spec=importlib.util.spec_from_file_location('recovery',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class RecoveryTests(unittest.TestCase):
 def test_default_never_reads_host(self):
  with patch.object(m,'command',side_effect=AssertionError('host call')),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(m.main([]),0)
  self.assertEqual(json.loads(out.getvalue())['status'],'offline_default')
 def test_complete_requires_exact_size_and_digest(self):
  self.assertTrue(m.first_complete({'present':True,'bytes':169445888,'sha256':m.ARTIFACTS[0][3]}))
  self.assertFalse(m.first_complete({'present':False}))
  self.assertFalse(m.first_complete({'present':True,'bytes':79641600,'sha256':'859cbdccb54fea793d1a4b2ba45e747081bfaf90c5f3b34ce1a341ec7092a27a'}))
  self.assertFalse(m.first_complete({'present':True,'bytes':169445888,'sha256':'0'*64}))
  with self.assertRaises(m.Failed):m.first_complete({'present':True,'bytes':169445889,'sha256':'0'*64})
 def test_nonzero_exit_keeps_fixed_reason_exit_and_counts(self):
  with self.assertRaises(m.Failed) as ctx:m.command([sys.executable,'-c','import sys;sys.stderr.write("synthetic-private-text");sys.exit(7)'],2)
  e=ctx.exception;self.assertEqual(e.code,'command_exit_nonzero');self.assertEqual(e.metrics['exit_code'],7)
  self.assertEqual(e.metrics['stderr_bytes'],22);self.assertNotIn('synthetic-private-text',str(e.metrics));self.assertGreaterEqual(e.metrics['elapsed_seconds'],0)
 def test_timeout_and_output_cap_have_measured_receipts(self):
  for script,limit,code in [('import time;time.sleep(3)',.3,'command_timeout'),('import os;os.write(1,b"x"*10000)',2,'command_output_limit')]:
   with self.subTest(code=code),self.assertRaises(m.Failed) as ctx:m.command([sys.executable,'-c',script],limit,cap=100)
   self.assertEqual(ctx.exception.code,code);self.assertIn('elapsed_seconds',ctx.exception.metrics);self.assertIn('exit_code',ctx.exception.metrics)
 def test_success_returns_only_stdout_and_measures_stderr(self):
  raw,metrics=m.command([sys.executable,'-c','import sys;print("{}",end="");sys.stderr.write("note")'],2)
  self.assertEqual(raw,b'{}');self.assertEqual(metrics['exit_code'],0);self.assertEqual(metrics['stderr_bytes'],4)
 def test_consumed_guard_preserves_prior_receipt_and_blocks_commands(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);script=root/'wtyj/scripts/recovery.py';script.parent.mkdir(parents=True);script.write_text('synthetic')
   guard=root/'tmp/isluno-staging-c';guard.mkdir(parents=True);prior=guard/'receipt.json';prior.write_text('original')
   with patch.object(m,'__file__',str(script)),patch.object(m,'ARTIFACTS',[]),patch.object(m,'command',side_effect=AssertionError('host call')),contextlib.redirect_stdout(io.StringIO()) as out:
    self.assertEqual(m.main(['--execute-approved-recovery','--approval-reference',m.REFERENCE]),1)
   self.assertEqual(prior.read_text(),'original');self.assertEqual(list(guard.iterdir()),[prior]);self.assertEqual(json.loads(out.getvalue())['code'],'recovery_already_dispatched')
 def test_exact_dynamic_payload_is_preserved_before_dispatch(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);raw=b'print("synthetic payload")\n'
   record=m.preserve_payload(root,'inspect_only_first_archive',raw)
   self.assertEqual((root/record['payload_file']).read_bytes(),raw)
   self.assertEqual(record['payload_sha256'],hashlib.sha256(raw).hexdigest())
   self.assertEqual(record['payload_bytes'],len(raw))
   self.assertEqual((root/record['payload_file']).stat().st_mode & 0o777,0o600)
   with self.assertRaises(FileExistsError):m.preserve_payload(root,'inspect_only_first_archive',b'changed')
   self.assertEqual((root/record['payload_file']).read_bytes(),raw)
   with self.assertRaises(m.Failed):m.preserve_payload(root,'../other',raw)
 def test_remote_projector_preserves_bytes_and_rejects_links(self):
  import ast
  tree=ast.parse(m.REMOTE_COMMON)
  nodes=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) or isinstance(n,ast.FunctionDef) and n.name in ('require','project')]
  env={};exec(compile(ast.Module(body=nodes,type_ignores=[]),'<offline-projector>','exec'),env)
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'archive';p.write_bytes(b'synthetic-public-archive');before=p.read_bytes()
   self.assertEqual(env['project'](p,100),{'bytes':len(before),'sha256':hashlib.sha256(before).hexdigest()});self.assertEqual(p.read_bytes(),before)
   link=p.with_name('link');link.symlink_to(p)
   with self.assertRaises(OSError):env['project'](link,100)
   with self.assertRaises(ValueError):env['project'](p,1)

if __name__=='__main__':
 import socket
 def deny(*a,**kw):raise AssertionError('network denied')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
 unittest.main(verbosity=2)
