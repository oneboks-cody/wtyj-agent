"""Generate six-language receipt/ticket review artifacts with every external socket denied."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='output/pdf/isluno-08')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    sys.path[:0]=[str(root/'wtyj'),str(root/'wtyj/tests/isluno')]
    def denied(*args,**kwargs):raise RuntimeError('External network disabled for quote samples')
    socket.socket.connect=socket.socket.connect_ex=socket.socket.sendto=denied
    socket.create_connection=socket.getaddrinfo=denied
    with tempfile.TemporaryDirectory(prefix='isluno-quote-samples-') as directory:
        path=Path(directory)/'client.json'
        path.write_text(json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'features':{},'channel_account_allowlist':{'mode':'strict','zernio_accounts':['fixture-account']}}))
        os.environ['CLIENT_CONFIG_PATH']=str(path)
        os.environ['ANTHROPIC_API_KEY']='offline-dummy-key'
        from test_payments import PaymentTests
        from test_conversation import response
        from agents.social.isluno_payment_copy import COPY
        from pypdf import PdfReader
        from unittest.mock import patch
        output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
        manifest=[]
        for language in COPY:
            case=PaymentTests();case.setUp()
            counter=[0]
            def token_hex(size=32):
                counter[0]+=1
                return hashlib.sha256((language+'/'+str(counter[0])).encode()).hexdigest()[:size*2]
            try:
                catalog=json.loads(case.catalog_path.read_text());product=catalog['products'][0]
                product['name']='Synthetic coastal expedition with island exploration and multilingual guest arrangements - ' * 2
                keys=('guest_rules','price_rules','schedule','options','pickup','policies')
                product['demo_rules']={'authority':'user_approved_demo_sample','version':'fixture-demo-v1','real_booking_eligible':False,
                    'label':'DEMO SAMPLE - Synthetic prices and rules, not supplier confirmed.','approval_ref':'Synthetic demo evidence',
                    'rules':{key:copy.deepcopy(product[key]) for key in keys}}
                case.catalog_path.write_text(json.dumps(catalog))
                with patch('secrets.token_hex',side_effect=token_hex):
                    for index in range(6):
                        guest=('Ana María José de Albuquerque van der Meer ' * 4).strip()
                        case.turn(response('add',[{'product_id':'fixture-cruise','date':f'2026-10-{15+index}'}],{'name':guest,'ages':[35,34,8]},document_language=language))
                    result,_=case.turn(response('summary'));summary=case.job(result);case.send_quote(summary['id'])
                    quote=case.tap(summary);case.send_quote(quote['id']);approved=case.tap(quote);case.send_quote(approved['id'])
                    case.pay(approved)
                record=case.payments.records(case.scope())[0]
                (output/('isluno-paid-'+language+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
                selected=[d for d in record['documents'] if d['kind']=='receipt']+[d for d in record['documents'] if d['kind']=='ticket'][:2]
                for index,document in enumerate(selected):
                    name='isluno-'+document['kind']+'-'+language+('-'+str(index) if document['kind']=='ticket' else '')+'.pdf'
                    destination=output/name;raw=case.payments.document(document['id']);destination.write_bytes(raw)
                    reader=PdfReader(destination);texts=[page.extract_text() for page in reader.pages]
                    text=' '.join(texts).replace('\n',' ')
                    assert 'USD 1500.00' in text if document['kind']=='receipt' else 'USD 250.00' in text
                    assert all('DEMO' in page for page in texts)
                    assert COPY[language][2] in text
                    manifest.append({'language':language,'kind':document['kind'],'file':name,'sha256':hashlib.sha256(raw).hexdigest(),
                                     'pages':len(texts),'bytes':len(raw),'items':6 if document['kind']=='receipt' else 1,
                                     'total_minor':150000 if document['kind']=='receipt' else 25000,'currency':'USD','synthetic_only':True})
            finally:case.doCleanups()
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
        print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
