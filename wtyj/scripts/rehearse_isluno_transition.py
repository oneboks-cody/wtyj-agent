"""Write evidence for a disposable network-denied cutover/rollback rehearsal."""
import argparse,json,os,socket,sys,tempfile
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    def denied(*args,**kwargs):raise RuntimeError('Network disabled for synthetic migration rehearsal')
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.socket.sendto=denied;socket.create_connection=denied;socket.getaddrinfo=denied
    root=Path(__file__).resolve().parents[2]
    sys.path[:0]=[str(root/'wtyj'),str(root/'wtyj/tests/isluno')]
    with tempfile.TemporaryDirectory(prefix='isluno-rehearsal-bootstrap-') as directory:
        config=Path(directory)/'client.json';config.write_text(json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'features':{}}))
        os.environ['CLIENT_CONFIG_PATH']=str(config);os.environ['ANTHROPIC_API_KEY']='offline-dummy-key'
        from test_recovery import RecoveryTests
        case=RecoveryTests('test_cutover_and_rollback_quarantine_legacy_callers_without_changing_history')
        try:
            case.setUp();case.test_cutover_and_rollback_quarantine_legacy_callers_without_changing_history()
            target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(case.rehearsal_evidence,indent=2)+'\n')
            print('Synthetic cutover/rollback passed; no external calls. Evidence:',target)
        finally:case.doCleanups()
if __name__=='__main__':main()
