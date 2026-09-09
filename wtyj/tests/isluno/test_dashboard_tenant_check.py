"""Synthetic HTTP/credential adapters only; no network or subprocess dispatch."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import time
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/check_isluno_dashboard_tenant.py'
spec=importlib.util.spec_from_file_location('dashboard_check',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SECRET='synthetic-token-CANARY'


class FixtureSocket:
    def __init__(self, raw):self.raw=raw
    def makefile(self,*args,**kwargs):return io.BytesIO(self.raw)


def wire(body=None,status=200,headers=None):
    body=json.dumps({'slug':'mermaid','name':'PRIVATE-NAME'}).encode() if body is None else body
    headers={'Content-Type':'application/json','Content-Length':str(len(body)),**(headers or {})}
    return (f'HTTP/1.1 {status} Fixture\r\n'+''.join(f'{k}: {v}\r\n' for k,v in headers.items())+'\r\n').encode()+body


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.requests=[];self.closes=0;self.raw=wire()
        owner=self
        class Connection:
            def __init__(self,host,port,timeout,context):
                owner.assertEqual((host,port),(m.HOST,443))
                owner.assertTrue(context.check_hostname);owner.assertEqual(context.verify_mode,m.ssl.CERT_REQUIRED)
                owner.assertLessEqual(timeout,10)
            def request(self,*args,**kwargs):owner.requests.append((args,kwargs))
            def getresponse(self):
                result=self.response_class(FixtureSocket(owner.raw),method='GET');result.begin();return result
            def close(self):owner.closes+=1
        self.factory=patch.object(m.http.client,'HTTPSConnection',Connection);self.factory.start();self.addCleanup(self.factory.stop)

    def run_check(self):return m.check(SECRET,time.monotonic()+10)

    def test_fixed_request_and_private_values_suppressed(self):
        result=self.run_check()
        self.assertTrue(result['tenant_matches'])
        self.assertEqual(set(result),{'status','http_status','tenant_matches','observed_at'})
        self.assertNotIn(SECRET,json.dumps(result));self.assertNotIn('PRIVATE-NAME',json.dumps(result))
        args,kwargs=self.requests[0]
        self.assertEqual(args,('GET',m.PATH));self.assertNotIn('body',kwargs)
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer '+SECRET)
        self.assertEqual(len(self.requests),1);self.assertEqual(self.closes,1)

    def test_redirect_and_401_never_retry(self):
        for code in (301,302,303,307,308,401,403,500):
            self.requests.clear();self.raw=wire(status=code,headers={'Location':'https://evil.invalid/?secret='+SECRET})
            with self.subTest(code=code),self.assertRaises(m.Stopped):self.run_check()
            self.assertEqual(len(self.requests),1)

    def test_wrong_slug_shapes_and_duplicate_keys(self):
        for body in (b'[]',b'{"slug":"other"}',b'{"slug":true}',b'{"slug":"mermaid","secret":"CANARY"}',
                     b'{"slug":"mermaid","slug":"mermaid"}',b'{"slug":"mermaid","business":[]}',
                     b'{"slug":"mermaid","name":{}}',b'{"slug":"mermaid","name":NaN}',b'CANARY',b'\xff'):
            self.raw=wire(body)
            with self.subTest(body=body),self.assertRaises(Exception):self.run_check()

    def test_total_headers_and_body_cap(self):
        for response in (wire(b'x'*8193),wire(headers={'X-Filler':'x'*8192}),
                         wire(b'{"slug":"mermaid","name":"'+b'x'*4000+b'"}',headers={'X-Filler':'y'*4300})):
            self.raw=response
            with self.assertRaises(m.Stopped):self.run_check()

    def test_chunked_and_short_read(self):
        body=b'{"slug":"mermaid"}'
        self.raw=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n'+format(len(body),'x').encode()+b'\r\n'+body+b'\r\n0\r\n\r\n'
        self.assertTrue(self.run_check()['tenant_matches'])
        class Short(io.BytesIO):
            def read(self,n=-1):return super().read(min(n,2))
        reader=m.BudgetReader(Short(b'abcdef'));self.assertEqual(reader.read(6),b'abcdef')
        self.raw=wire(headers={'Content-Encoding':'gzip'})
        with self.assertRaises(m.Stopped):self.run_check()

    def test_ambiguous_framing_rejected(self):
        for header in (b'Content-Length: 18\r\nContent-Length: 18\r\n',
                       b'Content-Length: 18\r\nTransfer-Encoding: chunked\r\n',
                       b'Transfer-Encoding: gzip\r\n'):
            self.raw=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'+header+b'\r\n{"slug":"mermaid"}'
            with self.assertRaises(m.Stopped): self.run_check()

    def test_deadline_and_transport_errors_redacted(self):
        with self.assertRaises(m.Stopped):m.check(SECRET,time.monotonic()-1)
        for error in (TimeoutError(SECRET),OSError('PRIVATE-NAME'),m.Stopped()):
            with patch.object(m,'credential_from_stdin',return_value=SECRET),patch.object(m,'check',side_effect=error),contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(m.main(['--execute-authorized-request','--authorization-reference','calvin-dashboard-tenant-once']),1)
            self.assertEqual(json.loads(out.getvalue()),{'status':'stopped','check':'dashboard_tenant'})

    def test_real_deadline_signal_handler_is_installed_and_restored(self):
        handlers={}
        def install(sig,handler):handlers[sig]=handler;return signal.SIG_DFL
        def expire(*args):handlers[signal.SIGALRM](None,None)
        with patch.object(m.signal,'signal',side_effect=install),patch.object(m.signal,'setitimer') as timer,patch.object(m,'credential_from_stdin',side_effect=expire),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(['--execute-authorized-request','--authorization-reference','calvin-dashboard-tenant-once']),1)
        self.assertEqual(timer.call_args_list[0].args,(signal.ITIMER_REAL,10))
        self.assertEqual(timer.call_args_list[-1].args,(signal.ITIMER_REAL,0))
        self.assertEqual(handlers[signal.SIGALRM],signal.SIG_DFL)
        self.assertNotIn(SECRET,out.getvalue());self.assertFalse(self.requests)

    def test_offline_default_and_authority_preflight(self):
        with patch.object(m,'credential_from_stdin',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]),0)
        self.assertEqual(json.loads(out.getvalue()),{'status':'offline_default','host_contacted':False})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(['--execute-authorized-request']),1)
            self.assertEqual(m.main(['--unknown-CANARY']),1)
        self.assertNotIn('CANARY',out.getvalue());self.assertFalse(self.requests)

    def test_protected_input_pipe_only_and_header_injection(self):
        for mode in (stat.S_IFREG,stat.S_IFCHR,stat.S_IFDIR):
            with patch.object(m.os,'fstat',return_value=type('S',(),{'st_mode':mode})()),self.assertRaises(m.Stopped):m.credential_from_stdin()
        for value in (b'',b'a\r\nX-Evil: b',b'a\n',b'x'*4097,b'\xff'):
            with patch.object(m.os,'fstat',return_value=type('S',(),{'st_mode':stat.S_IFIFO})()),patch.object(m.os,'read',side_effect=[value,b'']),self.assertRaises(Exception):m.credential_from_stdin()
        with patch.object(m.os,'fstat',return_value=type('S',(),{'st_mode':stat.S_IFIFO})()),patch.object(m.os,'read',side_effect=[SECRET.encode(),b'']):self.assertEqual(m.credential_from_stdin(),SECRET)
        for token in ('', 'abc\n','abc\r\nX-Evil: b'):
            with self.assertRaises(m.Stopped):m.check(token,time.monotonic()+10)
        self.assertFalse(self.requests)


if __name__=='__main__':unittest.main()
