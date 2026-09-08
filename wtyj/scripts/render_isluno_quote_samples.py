"""Generate six synthetic quote review artifacts with every external socket denied."""
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
    parser.add_argument('--output',default='output/pdf/isluno-07')
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
        from test_quotes import QuoteTests
        from test_conversation import response
        from agents.social.isluno_quote_documents import render_pdf,projection,text_sections,COPY
        from pypdf import PdfReader
        case=QuoteTests();case.setUp()
        try:
            catalog=json.loads(case.catalog_path.read_text())
            product=catalog['products'][0]
            product['name']='Synthetic coastal expedition with island exploration and multilingual guest arrangements - ' * 2
            keys=('guest_rules','price_rules','schedule','options','pickup','policies')
            product['demo_rules']={'authority':'user_approved_demo_sample','version':'fixture-demo-v1','real_booking_eligible':False,
                'label':'DEMO SAMPLE - Synthetic prices and rules, not supplier confirmed.','approval_ref':'Synthetic demo evidence',
                'rules':{key:copy.deepcopy(product[key]) for key in keys}}
            case.catalog_path.write_text(json.dumps(catalog))
            guest=('Ana María José de Albuquerque van der Meer ' * 4).strip()
            for index in range(6):
                case.turn(response('add',[{'product_id':'fixture-cruise','date':f'2026-10-{15+index}','guest_ages':[35,34,8]}],{'name':guest,'ages':[35,34,8]}))
            case.turn(response('summary'))
            snapshot=case.quotes.list(case.scope())[0]['snapshot']
            output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
            manifest=[]
            for language in COPY:
                sample=copy.deepcopy(snapshot)
                sample['id']=hashlib.sha256(('isluno-demo-review-'+language).encode()).hexdigest()[:32]
                sample['document_language']=language
                raw=render_pdf(sample)
                destination=output/('isluno-quote-'+language+'.pdf');destination.write_bytes(raw)
                reader=PdfReader(destination)
                pages=[page.extract_text() for page in reader.pages]
                text='\n'.join(pages)
                assert 'USD 1500.00' in text
                assert all('DEMO' in page for page in pages)
                assert COPY[language][18] in text
                assert guest in text.replace('\n',' ') or guest.split()[0] in text
                assert len(pages)>1
                (output/('isluno-quote-'+language+'.json')).write_text(json.dumps({'snapshot':sample,'projection':projection(sample),'summary':text_sections(sample)},ensure_ascii=False,indent=2))
                manifest.append({'language':language,'file':destination.name,'sha256':hashlib.sha256(raw).hexdigest(),'pages':len(pages),'bytes':len(raw),
                    'items':6,'currency':'USD','total_minor':150000,'demo_labels_every_page':True,'synthetic_only':True})
            (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
            print(json.dumps(manifest,indent=2))
        finally:case.doCleanups()


if __name__=='__main__':main()
