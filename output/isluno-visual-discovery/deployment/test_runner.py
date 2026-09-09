import hashlib,os,stat,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock
class Rejected(Exception):pass
def require(value,reason):
 if not value:raise Rejected(reason)
class RunnerTests(unittest.TestCase):
 def setUp(self):
  self.ns={'Path':Path,'BACKEND':'fixture-image','os':os,'stat':stat,'time':time,'require':require,'checked_read':lambda p,n:Path(p).read_bytes()}
  exec((Path(__file__).parent/'rollout.py').read_text(),self.ns)
 def test_abort_rejects_activation_and_unknown_runtime_without_mutation(self):
  spy=Mock();self.ns.update(cmd=spy,STATE={'image_changed':True,'sealed':False})
  with self.assertRaisesRegex(Rejected,'abort_after_cutover'):self.ns['abort_before_activation']()
  self.ns.update(STATE={'image_changed':False,'sealed':False},inspect=lambda n:{'Id':'other','Image':'fixture-image','State':{'Running':True}})
  with self.assertRaisesRegex(Rejected,'abort_runtime_changed'):self.ns['abort_before_activation']()
  spy.assert_not_called()
 def test_restore_enforces_budget_and_sealed_context(self):
  spy=Mock();self.ns.update(cmd=spy,STATE={'image_changed':False,'sealed':True},END=time.monotonic()+20)
  with self.assertRaisesRegex(Rejected,'restore_budget_unavailable'):self.ns['restore_original_after_pre_activation_failure']()
  self.ns.update(END=time.monotonic()+120,inspect=lambda n:{})
  with self.assertRaisesRegex(Rejected,'restore_context_missing'):self.ns['restore_original_after_pre_activation_failure']()
  spy.assert_not_called()
 def test_semantic_mount_order_does_not_hide_changed_flags(self):
  fn=self.ns['semantic_host_config'];a={'Binds':['b','a'],'Privileged':False};b={'Binds':['a','b'],'Privileged':False}
  self.assertEqual(fn(a),fn(b));b['Privileged']=True;self.assertNotEqual(fn(a),fn(b))

class CatalogRunnerTests(unittest.TestCase):
 def setUp(self):
  import json,tarfile
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  root=Path(self.temp.name);stage=root/'stage';stage.mkdir()
  with tarfile.open(Path(__file__).parent/'source.tar') as archive:
   for name in ['catalog.json','catalog-before.json']:(stage/name).write_bytes(archive.extractfile(name).read())
  target=root/'isluno_catalog.json';target.write_bytes((stage/'catalog-before.json').read_bytes())
  self.ns={'Path':Path,'BACKEND':'fixture-image','os':os,'stat':stat,'time':time,'require':require,'checked_read':lambda p,n:Path(p).read_bytes(),'json':json,'hashlib':hashlib,'digest':lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()}
  exec((Path(__file__).parent/'rollout.py').read_text(),self.ns)
  self.ns.update(STAGE=stage,CLIENT=root/'client.json',STATE={},MANIFEST=json.loads((Path(__file__).parent/'files.json').read_text()))
  self.target=target
 def test_only_approved_catalog_context_changes(self):
  self.ns['catalog_binding']()
  import json
  path=self.ns['STAGE']/'catalog.json';value=json.loads(path.read_text());value['products'][0]['name']='Unapproved change';path.write_text(json.dumps(value))
  with self.assertRaisesRegex(Rejected,'catalog_unrelated_change'):self.ns['catalog_binding']()
 def test_catalog_swap_and_one_restore_preserve_exact_bytes(self):
  before=self.target.read_bytes();self.ns['select_catalog']();self.assertTrue(self.ns['STATE']['catalog_changed'])
  self.assertEqual(self.target.read_bytes(),(self.ns['STAGE']/'catalog.json').read_bytes())
  self.ns['select_catalog'](restore=True);self.assertEqual(self.target.read_bytes(),before);self.assertFalse(self.ns['STATE']['catalog_changed'])
 def test_unknown_catalog_is_not_overwritten(self):
  self.target.write_text('unknown')
  with self.assertRaisesRegex(Rejected,'catalog_changed_outside_release'):self.ns['select_catalog']()
  self.assertEqual(self.target.read_text(),'unknown')

if __name__=='__main__':unittest.main()
