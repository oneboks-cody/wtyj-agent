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

class FrontendRunnerTests(unittest.TestCase):
 def setUp(self):
  self.ns={'Path':Path,'BACKEND':'fixture-image','os':os,'stat':stat,'time':time,'require':require,'checked_read':lambda p,n:Path(p).read_bytes()}
  exec((Path(__file__).parent/'rollout.py').read_text(),self.ns)
 def test_atomic_frontend_switch_and_restore_preserve_both_releases(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);old=root/'old';new=root/'new';old.mkdir();new.mkdir();(old/'index.html').write_text('old');(new/'index.html').write_text('new')
   pointer=root/'current';pointer.symlink_to(old)
   state={};self.ns.update(DASHBOARD=pointer,DASHBOARD_TARGET=str(old),DASHBOARD_NEW=str(new),STATE=state)
   def binding():self.assertEqual(os.readlink(pointer),str(new if state.get('frontend_changed') else old))
   self.ns['dashboard_binding']=binding
   self.ns['frontend_switch']();binding();self.assertTrue(state['frontend_changed'])
   self.ns['frontend_switch'](restore=True);binding();self.assertFalse(state['frontend_changed'])
   self.assertEqual((old/'index.html').read_text(),'old');self.assertEqual((new/'index.html').read_text(),'new')
 def test_public_assets_require_exact_content(self):
  import io
  from types import SimpleNamespace
  bodies={'frontend/index.html':b'index','frontend/assets/index-a.js':b'entry','frontend/assets/IslunoOperations-a.js':b'operations'}
  manifest={'files':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in bodies.items()}}
  urls=[]
  def read(url,timeout):
   self.assertEqual(timeout,5);self.assertTrue(url.startswith('https://dashboard.unboks.org/'));urls.append(url)
   value=io.BytesIO(bodies['frontend/'+url.split('.org/')[1]]);value.status=200;return value
  self.ns.update(MANIFEST=manifest,hashlib=hashlib,urllib=SimpleNamespace(request=SimpleNamespace(urlopen=read)),event=Mock())
  self.ns['public_frontend_ready']();self.assertEqual(len(urls),3)
  bodies['frontend/index.html']=b'wrong'
  with self.assertRaisesRegex(Rejected,'public_asset_hash'):self.ns['public_frontend_ready']()

if __name__=='__main__':unittest.main()
