"""Synthetic metadata/files only. No SSH, provider or application runtime imports."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2]/'scripts/inspect_isluno_target.py'
spec=importlib.util.spec_from_file_location('inspection',SCRIPT)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
ID='a'*64; IMAGE='sha256:'+'b'*64
MOUNTS='\n'.join('bind|'+m.ROOT+'/'+n+'|/app/'+n+'|true' for n in ('config','data','logs'))
PATHS=['agents/file_'+str(i)+'.py' for i in range(112)]


class InspectionTests(unittest.TestCase):
    def setUp(self):
        # No child may invoke SSH/Docker; Python fixture children also deny sockets.
        original=m.subprocess.Popen
        bootstrap="import socket\ndef denied(*a,**k): raise RuntimeError('network disabled')\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\nsocket.socket.sendto=denied\nsocket.create_connection=denied\nsocket.getaddrinfo=denied\n"
        def guarded(argv,**kwargs):
            self.assertEqual(argv[0],sys.executable)
            argv=list(argv)
            if '-c' in argv:
                i=argv.index('-c')+1;argv[i]=bootstrap+argv[i]
            else:
                self.assertEqual(argv[-1],'-')
                argv=argv[:-1]+['-c',bootstrap+"import sys;exec(compile(sys.stdin.read(),'fixture-stdin','exec'))"]
            return original(argv,**kwargs)
        self.guard=patch.object(m.subprocess,'Popen',side_effect=guarded);self.guard.start();self.addCleanup(self.guard.stop)

    def test_default_never_dispatches(self):
        with patch.object(sys,'argv',['inspection']),patch.object(m,'bounded_command',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(),0)
        self.assertEqual(json.loads(out.getvalue())['host_contacted'],False)

    def test_missing_authority_never_dispatches(self):
        with patch.object(sys,'argv',['inspection','--execute-authorized-host']),patch.object(m,'bounded_command',side_effect=AssertionError):
            with self.assertRaises(m.Rejected):m.main()

    def test_identity_and_mounts(self):
        self.assertEqual(m.container_metadata(ID+'|/wtyj-mermaid|'+IMAGE+'|true|2026-09-08T13:38:46.139199144Z|agent')['image'],IMAGE)
        output=json.dumps(m.mount_metadata(MOUNTS));self.assertNotIn('/app/logs',output);self.assertNotIn('/mermaid/logs',output)
        self.assertTrue(json.loads(output)['expected_logs_mount_verified'])
        self.assertEqual(m.mount_metadata(MOUNTS),m.mount_metadata('\n'.join(reversed(MOUNTS.splitlines()))))
        for bad in [MOUNTS+'\n'+MOUNTS.splitlines()[0], MOUNTS.replace('/mermaid/logs','/other/logs'),MOUNTS.replace('/app/logs','/app/secret'),MOUNTS.replace('true','false'),MOUNTS.replace('bind','volume'),MOUNTS.replace('|true',''),MOUNTS.splitlines()[0]]:
            with self.subTest(bad=bad),self.assertRaises(m.Rejected):m.mount_metadata(bad)
        for bad in ['raw secret',ID+'|/other|'+IMAGE+'|true|2026-09-08T13:38:46Z|agent']:
            with self.assertRaises(m.Rejected):m.container_metadata(bad)

    def test_docker_template_terminal_framing(self):
        # {{println}} emits an LF per record; TemplateInspector adds one final LF.
        rows=MOUNTS.split('\n')
        docker_output=''.join(row+'\n' for row in rows)+'\n'
        self.assertEqual(docker_output,MOUNTS+'\n\n')
        expected=m.mount_metadata(MOUNTS)
        for suffix in ('','\n','\n\n'):
            self.assertEqual(m.mount_metadata(MOUNTS+suffix),expected)
        for bad in (MOUNTS+'\n\n\n', '\n'+docker_output,
                    docker_output.replace('\n','\n\n',1), MOUNTS+'\n \n',
                    MOUNTS.replace('\n','\r\n'), MOUNTS.replace('\n','\v'),
                    docker_output+rows[0]+'\n', docker_output.replace('/mermaid/logs','/other/logs')):
            with self.subTest(case=repr(bad)),self.assertRaises(m.Rejected):m.mount_metadata(bad)
        result=self.run_fixture()
        self.assertEqual(result['status'],'observed')
        self.assertNotIn('/app/logs',json.dumps(result))

    def test_safe_files_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();f=root/'x.py';f.write_bytes(b'12345')
            self.assertEqual(m.file_hash(str(f),[0]),m.hashlib.sha256(b'12345').hexdigest())
            with self.assertRaises(m.Rejected):m.file_hash(str(f),[0],file_limit=4)
            with self.assertRaises(m.Rejected):m.file_hash(str(f),[4],total_limit=8)
            budget=[0];m.file_hash(str(f),budget,total_limit=9)
            with self.assertRaises(m.Rejected):m.file_hash(str(f),budget,total_limit=9)
            link=root/'link';link.symlink_to(f)
            parent=root/'parent';parent.symlink_to(root,target_is_directory=True)
            for path in [link,parent/'x.py',root/'..'/'elsewhere']:
                with self.assertRaises((m.Rejected,OSError)):m.file_hash(str(path),[0])
            with patch.object(m.os,'read',side_effect=AssertionError('metadata must not read')):
                self.assertEqual(m.path_metadata(str(f))['bytes'],5)
            with self.assertRaises(m.Rejected):m.path_metadata(str(link))

    def test_bounded_process_and_failure_suppression(self):
        self.assertEqual(m.bounded_command([sys.executable,'-I','-c','print("ok")']), 'ok\n')
        for code,kwargs in [('import time; time.sleep(3)',{'timeout':.05}),('print("SECRET"*1000)',{'cap':32}),('import sys;sys.stderr.write("SECRET"*1000)',{'cap':32}),('import sys;print("SECRET");sys.exit(1)',{})]:
            with self.assertRaises(m.Rejected) as caught:m.bounded_command([sys.executable,'-I','-c',code],**kwargs)
            self.assertNotIn('SECRET',str(caught.exception))
        payload=b'x'*200000
        self.assertEqual(m.bounded_command([sys.executable,'-I','-c','import sys;print(len(sys.stdin.buffer.read()))'],data=payload),'200000\n')

    def test_manifest_and_ssh_policy(self):
        self.assertEqual(m.source_paths(PATHS),PATHS)
        for path in ['/etc/x.py','agents/../x.py','data/x.py','agents/x.py\nSECRET','config/x.py']:
            bad=PATHS.copy();bad[0]=path
            with self.assertRaises(m.Rejected):m.source_paths(bad)
        with self.assertRaises(m.Rejected):m.source_paths(PATHS[:-1])
        args=m.ssh_argv();self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('ConnectionAttempts=1',args)
        self.assertNotIn('StrictHostKeyChecking=no',args)

    def fake_run(self, argv, **kwargs):
        self.assertLessEqual(kwargs['timeout'],15);self.assertEqual(kwargs['cap'],65536)
        if argv==['uname','-s']:return 'Linux\n'
        if argv==['uname','-m']:return 'x86_64\n'
        if argv[:3]==['docker','image','inspect']:return IMAGE+'|linux|amd64\n'
        if argv[:2]==['docker','inspect']:
            self.assertEqual(argv[-1], ID if '.Mounts' in argv[3] else m.CONTAINER)
            return MOUNTS+'\n\n' if '.Mounts' in argv[3] else ID+'|/wtyj-mermaid|'+IMAGE+'|true|2026-09-08T13:38:46Z|agent\n'
        if argv[:2]==['docker','exec']:
            self.assertEqual(argv[3],ID)
            self.assertNotIn('import agents',kwargs['data'].decode())
            return json.dumps([dict(path=p,sha256='c'*64) for p in PATHS])
        raise AssertionError('unexpected dispatch')

    def run_fixture(self,runner=None,target=None):
        with patch.object(m,'path_metadata',side_effect=lambda p,d=False: dict(path=p,type='directory' if d else 'file',bytes=1,uid=0,gid=0,mode='0o755')),patch.object(m,'safe_open',return_value=12345),patch.object(m.os,'close'),patch.object(m.os,'readlink',return_value=target or m.RELEASES+'/'+'d'*40),patch.object(m,'file_hash',return_value='c'*64):
            return m.run_checks(PATHS,run=runner or self.fake_run)

    def test_full_h1_h7_fixture_and_escape(self):
        result=self.run_fixture();self.assertEqual(result['status'],'observed');self.assertEqual(set(result['checks']),{'H'+str(i) for i in range(1,8)})
        result=self.run_fixture(target='/etc/secret');self.assertEqual(result,{'status':'stopped','failed_check':'H6','checks':{}})

    def test_raw_failure_suppressed(self):
        def fail(*args,**kwargs):raise RuntimeError('SECRET\nCUSTOMER CONTENT')
        result=self.run_fixture(runner=fail);self.assertEqual(result,{'status':'stopped','failed_check':'H1','checks':{}})
        self.assertNotIn('SECRET',json.dumps(result))
        def fail_h7(argv,**kwargs):
            if argv[:2]==['docker','exec']:return '[{"secret":"RAW"}]'
            return self.fake_run(argv,**kwargs)
        result=self.run_fixture(runner=fail_h7);self.assertEqual(result['failed_check'],'H7');self.assertEqual(result['checks'],{})

    def test_remote_payload_and_hash_worker(self):
        baseline=json.loads((SCRIPT.parents[2]/'wtyj/briefs/isluno_baseline_manifest.json').read_text())
        actual=[x['path'] for x in baseline['backend']['runtime']['python_files']]
        payload=m.remote_payload(actual).decode();compile(payload,'remote-fixture','exec')
        self.assertNotIn('import inspect\n',payload)
        self.assertIn('signal.alarm(15)',m.HASH_WORKER)
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp).resolve()/'fixture.py';f.write_text('synthetic')
            code=m.HASH_WORKER+'\nprint(file_hash('+repr(str(f))+',[0]))\n'
            value=m.bounded_command([sys.executable,'-I','-B','-'],data=code.encode())
            self.assertEqual(value.strip(),m.hashlib.sha256(b'synthetic').hexdigest())

    def test_result_whitelist_and_main_suppression(self):
        value=self.run_fixture();self.assertEqual(m.validate_result(value,PATHS),value)
        for bad in [{'status':'stopped','checks':{},'failed_check':'RAW SECRET'},
                    {'status':'observed','checks':{'raw':'SECRET'}}]:
            with self.assertRaises(m.Rejected):m.validate_result(bad,PATHS)
        args=['inspection','--execute-authorized-host','--authorization-reference','synthetic-only']
        with patch.object(sys,'argv',args),patch.object(m,'bounded_command',return_value='{"status":"observed","checks":{"secret":"RAW"}}'),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(),1)
        self.assertEqual(json.loads(out.getvalue()),{'status':'stopped','failed_check':'transport','checks':{}})
        self.assertNotIn('RAW',out.getvalue())

    def test_session_deadline_and_wrong_image(self):
        with patch.object(m.time,'monotonic',side_effect=[0,121]):
            self.assertEqual(self.run_fixture()['failed_check'],'H1')
        def wrong(argv,**kwargs):
            if argv[:3]==['docker','image','inspect']:return IMAGE+'|linux|arm64'
            return self.fake_run(argv,**kwargs)
        self.assertEqual(self.run_fixture(runner=wrong)['failed_check'],'H3')

    def test_exited_parent_descendant_cannot_continue(self):
        # Each child proves it started; after failure its later side effect must not occur.
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ('timeout','output','failure'):
                ready=Path(tmp)/(mode+'-ready'); marker=Path(tmp)/(mode+'-late')
                code="import os,time,sys\nfrom pathlib import Path\nchild=os.fork()\nif child==0:\n Path("+repr(str(ready))+").write_text('started')\n time.sleep(.35)\n Path("+repr(str(marker))+").write_text('late')\n os._exit(0)\nwhile not Path("+repr(str(ready))+").exists(): time.sleep(.001)\n"
                if mode=='output':code+="print('x'*10000,flush=True)\nos._exit(0)\n"
                elif mode=='failure':
                    # Close child's pipe copies so parent failure can be observed immediately.
                    code=code.replace(" time.sleep(.35)"," os.close(1);os.close(2)\n time.sleep(.35)")
                    code+="os._exit(1)\n"
                else:code+="os._exit(0)\n"
                with self.subTest(mode=mode),self.assertRaises(m.Rejected):
                    m.bounded_command([sys.executable,'-I','-c',code],timeout=.2,cap=1000)
                self.assertTrue(ready.exists())
                time.sleep(.45)
                self.assertFalse(marker.exists(), 'descendant performed work after rejected call')

    def test_container_replacement_restart_and_stop(self):
        for mode in ('replacement','restart','stopped','image'):
            names=[]
            def changed(argv,**kwargs):
                text=self.fake_run(argv,**kwargs)
                if argv[:2]==['docker','inspect'] and argv[-1]==m.CONTAINER:
                    names.append(argv[-1])
                    if len(names)==2:
                        if mode=='replacement':return text.replace(ID,'e'*64)
                        if mode=='restart':return text.replace('13:38:46Z','13:39:46Z')
                        if mode=='stopped':return text.replace('|true|','|false|')
                        return text.replace(IMAGE,'sha256:'+'f'*64)
                return text
            with self.subTest(mode=mode):
                result=self.run_fixture(runner=changed)
                self.assertEqual(len(names),2)
                self.assertEqual(result,{'status':'stopped','failed_check':'H7','checks':{}})

    def test_network_denied(self):
        with self.assertRaises(RuntimeError):socket.create_connection(('example.com',443))


if __name__=='__main__':
    def denied(*args,**kwargs):raise RuntimeError('network disabled')
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied;socket.getaddrinfo=denied
    unittest.main(verbosity=2)
