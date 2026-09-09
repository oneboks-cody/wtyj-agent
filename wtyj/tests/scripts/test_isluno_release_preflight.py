"""Offline corrected-preflight contract and synthetic complete preflight checks."""
import contextlib,io,copy,hashlib,importlib.util,json,os,sys,tempfile,time,types,unittest
from pathlib import Path,PurePosixPath
from unittest.mock import patch
SCRIPT=Path(__file__).resolve().parents[2]/'scripts/preflight_isluno_release.py'
spec=importlib.util.spec_from_file_location('candidate',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NG='''# configuration file /etc/nginx/nginx.conf:
events {} http { include /etc/nginx/sites-enabled/*; }
# configuration file /etc/nginx/sites-enabled/api:
server { listen 443 ssl; server_name api.unboks.org;
location ^~ /api/mermaid/ { proxy_set_header X-Tenant-Slug mermaid; proxy_pass http://127.0.0.1:8102/; } }
# configuration file /etc/nginx/sites-enabled/dashboard:
server { listen 443 ssl; server_name dashboard.unboks.org; root /var/www/unboks-dashboard/current; location / { try_files $uri $uri/ /index.html; } }
'''
EXPECTED=[{'source':'/root/clients/mermaid/'+n,'destination':'/app/'+n,'rw':True} for n in ('config','data','logs')]

class Contract(unittest.TestCase):
 def test_default_does_not_inspect_host(self):
  with patch.object(m,'main',side_effect=AssertionError('host read')),contextlib.redirect_stdout(io.StringIO()) as out:m.entry([])
  self.assertEqual(json.loads(out.getvalue())['status'],'offline_default')
 def test_compose_exact_order_and_rejections(self):
  a='/root/unboks-internal-control-panel/docker-compose.yml';b='/root/unboks-internal-control-panel/docker-compose.override.yml'
  self.assertEqual(m.selected_icp_compose_files(a+','+b),[a,b])
  for bad in [None,'',a,b+','+a,a+','+a,a+','+b+',/tmp/extra.yml','/tmp/other.yml',' '+a+','+b]:
   with self.subTest(bad=bad),self.assertRaises(m.Stop):m.selected_icp_compose_files(bad)
 def test_mount_exact_duplicate_extra_mode_and_path(self):
  m.validate_mounts(EXPECTED,EXPECTED)
  for bad in [EXPECTED+[EXPECTED[0]],EXPECTED[:2],EXPECTED[:2]+[EXPECTED[0]],[dict(x,rw=False) for x in EXPECTED],[dict(x,source='/tmp') for x in EXPECTED]]:
   with self.subTest(bad=bad),self.assertRaises(m.Stop):m.validate_mounts(bad,EXPECTED)
 def test_nginx_loaded_server_location_upstream_binding(self):
  self.assertEqual(m.validate_nginx(NG)['tenant_header'],'mermaid')
  for bad in [NG.replace('8102','8103'),NG.replace('X-Tenant-Slug mermaid','X-Tenant-Slug other'),NG.replace('server_name api.unboks.org','server_name wrong.example'),NG.replace('location ^~ /api/mermaid/','location ^~ /api/other/'),NG.replace('proxy_pass http://127.0.0.1:8102/;','proxy_pass http://127.0.0.1:8102/; rewrite ^ /other;'),NG.replace('sites-enabled/*','absent/*')]:
   with self.subTest(bad=bad),self.assertRaises(m.Stop):m.validate_nginx(bad)
 def test_nginx_empty_optional_glob_and_required_literal(self):
  valid=NG.replace('http {','http { include /etc/nginx/conf.d/*.conf;')
  self.assertEqual(m.validate_nginx(valid)['tenant_header'],'mermaid')
  for bad in [NG.replace('http {','http { include /etc/nginx/required.conf;'),NG.replace('sites-enabled/*','missing-selected/*.conf')]:
   with self.subTest(bad=bad),self.assertRaises(m.Stop):m.validate_nginx(bad)
 def test_nginx_effective_selected_routing_regressions(self):
  bads=[NG.replace('server_name api.unboks.org;','server_name api.unboks.org; '+x) for x in ['rewrite ^ /api/ali/ last;','return 302 https://wrong.example;','if ($host) { return 302 /other; }']]
  bads += [NG.replace('listen 443 ssl','listen 8443 ssl'),NG.replace('try_files $uri $uri/ /index.html;','proxy_pass http://127.0.0.1:9999/;'),NG.replace('try_files $uri $uri/ /index.html;','try_files $uri $uri/ /other.html;'),NG.replace('proxy_pass http://127.0.0.1:8102/;','proxy_pass http://127.0.0.1:8102/; if ($host) { return 302 /other; }')]
  for bad in bads:
   with self.subTest(bad=bad),self.assertRaises(m.Stop):m.validate_nginx(bad)
 def test_nginx_source_defined_options_guard_is_supported(self):
  valid=NG.replace('proxy_pass http://127.0.0.1:8102/;','proxy_pass http://127.0.0.1:8102/; if ($request_method = OPTIONS) { add_header Access-Control-Max-Age 86400 always; return 204; }')
  self.assertEqual(m.validate_nginx(valid)['tenant_header'],'mermaid')
  with self.assertRaises(m.Stop):m.validate_nginx(valid.replace('return 204;','return 302 /other;'))
 def test_holder_inode_alias_and_known_classification(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);data=root/'root/clients/mermaid/data';config=root/'root/clients/mermaid/config';data.mkdir(parents=True);config.mkdir()
   f=data/'state_registry.db';f.touch()
   for pid,cgroup in [('100','/docker/'+'a'*64),('101','/unknown.service')]:
    p=root/'proc'/pid;(p/'fd').mkdir(parents=True);(p/'fd/3').symlink_to(f);(p/'cgroup').write_text(cgroup)
   realwalk=os.walk
   def path(*parts):
    p=Path(*parts)
    return root/str(p).lstrip('/') if str(p).startswith(('/proc','/root/clients')) else p
   with patch.object(m,'Path',path),patch.object(m.os,'walk',lambda b,**kw:realwalk(root/b.lstrip('/'),**kw)):
    result=m.selected_holders('a'*64,'b'*64,{'LoadState':'loaded'},{'unboks-tracy-watchdog.service':{'LoadState':'loaded'}})
   self.assertEqual(sorted(x['known_fenceable_producer'] for x in result),[False,True])
   self.assertFalse(any('path' in x for x in result))
 def test_stream_output_cap_and_time_limit(self):
  with self.assertRaises(m.Stop):m.bounded_command([sys.executable,'-c','import os;os.write(1,b"x"*10000)'],cap=200,timeout=2)
  before=time.monotonic()
  with self.assertRaises(m.Stop):m.bounded_command([sys.executable,'-c','import time;time.sleep(3)'],cap=200,timeout=.3)
  self.assertLess(time.monotonic()-before,1.5)

class FakePath:
 def __init__(self,*p):self.p=PurePosixPath(*[str(x) for x in p])
 def __str__(self):return str(self.p)
 def is_symlink(self):return str(self)=='/var/www/unboks-dashboard/current'
 def exists(self):return str(self).startswith('/etc/') or str(self)=='/var/spool/cron/crontabs'
 def resolve(self):return FakePath('/var/www/unboks-dashboard/releases/2e41f4a209fbafc602419408696a25fa9791dc34')
 def iterdir(self):return iter([])

class DB:
 def execute(self,sql,args=()):return self
 def fetchall(self):return [('mermaid','whatsapp','zernio','connected',1)]
 def set_progress_handler(self,*a):pass
 def close(self):pass

class CompletePreflight(unittest.TestCase):
 def setup_objects(self):
  observed={'mermaid':{'id':'a'*64,'name':'wtyj-mermaid','image':'sha256:ed146176bbf4e116270b55a04771af5fb3cd5d0f62d124d8650aa7e1bac97552','pid':100,'compose_service':'agent','compose_files':'/root/clients/mermaid/docker-compose.yml','compose_working_dir':'/root/clients/mermaid','mounts':copy.deepcopy(EXPECTED)},
  'icp':{'id':'b'*64,'name':'unboks-internal-control-panel-wtyj-admin-1','image':'sha256:'+'c'*64,'pid':101,'compose_service':'wtyj-admin','compose_files':'/root/unboks-internal-control-panel/docker-compose.yml,/root/unboks-internal-control-panel/docker-compose.override.yml','compose_working_dir':'/root/unboks-internal-control-panel','mounts':[{'source':'/root/unboks-internal-control-panel/data','destination':'/app/data','rw':True},{'source':'/root/clients','destination':'/app/tenant_root','rw':False}]}}

  def expand(s):return {'Id':s['id'],'Name':'/'+s['name'],'Image':s['image'],'State':{'Running':True,'Pid':s['pid']},'Config':{'WorkingDir':'/app','Env':[],'Labels':{'com.docker.compose.service':s['compose_service'],'com.docker.compose.project.config_files':s['compose_files'],'com.docker.compose.project.working_dir':s['compose_working_dir']}},'Mounts':[{'Source':v['source'],'Destination':v['destination'],'RW':v['rw']} for v in s['mounts']],'NetworkSettings':{'Networks':{'synthetic-net':{}}}}
  return expand(observed['mermaid']),expand(observed['icp'])
 def exercise(self,mutate=lambda a,b:None):
  mm,icp=self.setup_objects();mutate(mm,icp);calls=[]
  def inspect(name):return mm if name=='wtyj-mermaid' else icp
  def run(args,cap=2097152):
   calls.append(args)
   if args[:2]==['docker','ps']:return json.dumps({'ID':'icp','Ports':'127.0.0.1:8010->8010/tcp'})
   if args[:2]==['docker','exec']:return json.dumps({'sha256':'948001e21d0ed6b238caffa4a1d277197cc11e4f377311fba30d7e4ca527f112','router':True,'forward':True})
   if args==['nginx','-T']:return NG
   if args[0]=='systemctl':return 'LoadState=loaded\nActiveState=active\nMainPID=123\n'
   raise AssertionError(args)
  def read(p,cap=2097152):
   if p=='/root/clients/mermaid/config/client.json':return json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'features':{},'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic-account']}}).encode()
   if p=='/usr/local/lib/unboks/tracy_watchdog.py':return b"/root/clients/mermaid/.maintenance STATE.with_suffix('.lock') LOCK_EX"
   return b'synthetic config'
  def sha(raw):return 'ca117468b3369f1961ebc941f6774f539d98f82be488dd433c0a2df4eb6a8c09' if raw==b'synthetic-account' else hashlib.sha256(raw).hexdigest()
  with patch.multiple(m,inspect=inspect,run=run,read=read,sha=sha,Path=FakePath,validate_regular_path=lambda *a:None,selected_holders=lambda *a:[{'pid':123,'known_fenceable_producer':True}],START=time.monotonic(),RESULT={'status':'running','checks':{}}),patch.object(m.platform,'machine',return_value='x86_64'),patch.object(m.sqlite3,'connect',return_value=DB()),patch.object(m.os,'readlink',return_value='/var/www/unboks-dashboard/releases/2e41f4a209fbafc602419408696a25fa9791dc34'),patch.object(m.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=2**30,f_frsize=4096)):
   m.main();return copy.deepcopy(m.RESULT),calls
 def test_complete_synthetic_preflight(self):
  result,calls=self.exercise();self.assertEqual(result['status'],'pass');self.assertEqual(len(result['checks']),6)
  self.assertFalse(any(c[0] in ('ssh','scp') for c in calls))
 def test_bad_compose_stops_full_flow(self):
  def change(mm,icp):icp['Config']['Labels']['com.docker.compose.project.config_files']='/tmp/other.yml'
  with self.assertRaises(m.Stop):self.exercise(change)
 def test_wrong_mount_stops_full_flow(self):
  def change(mm,icp):mm['Mounts'][0]['RW']=False
  with self.assertRaises(m.Stop):self.exercise(change)

if __name__=='__main__':
 import socket
 def denied(*a,**kw):raise AssertionError('network denied')
 socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied;socket.getaddrinfo=denied
 unittest.main(verbosity=2)
