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

if __name__=="__main__":unittest.main()
