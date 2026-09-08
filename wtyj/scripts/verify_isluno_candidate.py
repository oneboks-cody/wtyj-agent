"""Export integrated synthetic evidence with all external sockets/DNS denied."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def denied(*args,**kwargs):raise RuntimeError('External networking denied for integrated candidate verification')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    socket.socket.connect=socket.socket.connect_ex=socket.socket.sendto=denied
    socket.create_connection=socket.getaddrinfo=denied
    sys.path[:0]=[str(ROOT/'wtyj'),str(ROOT/'wtyj/tests/isluno')]
    with tempfile.TemporaryDirectory(prefix='isluno-integrated-bootstrap-') as directory:
        path=Path(directory)/'client.json';path.write_text(json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'features':{}}))
        os.environ['CLIENT_CONFIG_PATH']=str(path);os.environ['ANTHROPIC_API_KEY']='offline-dummy-key'
        from test_integrated import IntegratedTests
        from pypdf import PdfReader
        from io import BytesIO
        case=IntegratedTests();case.setUp()
        try:
            case.test_new_journeys_and_ledgers_survive_cutover_rollback_without_replay()
            evidence=case.evidence
            evidence['backend_source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
            evidence['documents']=[]
            for name,data in sorted(case.documents.items()):
                target=output/name;target.write_bytes(data);pdf=PdfReader(BytesIO(data))
                evidence['documents'].append({'file':name,'sha256':digest(target),'pages':len(pdf.pages),'extractable_text':all(bool(p.extract_text().strip()) for p in pdf.pages)})
            assert all(d['extractable_text'] for d in evidence['documents'])
            (output/'integrated.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
            print(json.dumps({'integrated':'passed','documents':len(evidence['documents']),'new_tables_preserved':len(evidence['post_cutover_preserved']),'legacy_tables_preserved':len(evidence['legacy_preserved']),'external_calls':0,'rollback_extra_sends':0}))
        finally:case.doCleanups()
    return 0
if __name__=='__main__':raise SystemExit(main())
