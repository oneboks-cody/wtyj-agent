"""Local-only catalog/API fixture. No customer data, secrets or outbound sockets."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'wtyj'), str(ROOT / 'wtyj/tests/isluno')]


def denied(*args, **kwargs):
    raise RuntimeError('Outbound networking disabled in catalog fixture')


def build_fixture(directory):
    from PIL import Image
    from test_catalog import synthetic_catalog
    from shared.isluno_catalog import CatalogStore
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    catalog=synthetic_catalog(); original=catalog['products'][0]
    rule_keys=['guest_rules','price_rules','schedule','options','pickup','policies']
    original['demo_rules']={'authority':'user_approved_demo_sample','version':'fixture-v1','label':'DEMO SAMPLE — synthetic rules, not supplier-confirmed',
        'approval_ref':'Synthetic local fixture only','real_booking_eligible':False,'rules':{key:copy.deepcopy(original[key]) for key in rule_keys}}
    original['price_rules']=None; original['schedule']=None
    assets=directory/'assets'; assets.mkdir(exist_ok=True)
    for i,color in enumerate(['#097f8c','#e9b66f','#428d69']):
        from io import BytesIO
        raw=BytesIO(); Image.new('RGB',(480,320),color).save(raw,format='PNG'); data=raw.getvalue(); digest=hashlib.sha256(data).hexdigest()
        (assets/(digest+'.png')).write_bytes(data)
        original['gallery'].append({'id':f'image-{i}','order':i,'caption':f'Synthetic scene {i+1}','source_url':f'https://example.invalid/image-{i}.png',
            'validation_status':'verified','sha256':digest,'delivery_path':'assets/isluno/'+digest+'.png','format':'png','bytes':len(data),'width':480,'height':320})
    catalog['products']=[copy.deepcopy(original) for _ in range(31)]
    for i,product in enumerate(catalog['products']):
        product['id']=f'fixture-trip-{i+1:02}';product['name']=f'Synthetic Island Trip {i+1:02}';product['category']=['Boat tours','Island adventures','Nature'][i%3]
    path=directory/'isluno_catalog.json'; path.write_text(json.dumps(catalog))
    config={'slug':'mermaid','business':{'slug':'mermaid','name':'Synthetic Isluno'},'features':{'isluno_itinerary_demo_v1':True},
            'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic-account']}}
    config_path=directory/'client.json';config_path.write_text(json.dumps(config))
    profile=json.loads((ROOT/'clients/mermaid/config/isluno_profile.json').read_text());(directory/'isluno_profile.json').write_text(json.dumps(profile))
    os.environ.update(CLIENT_CONFIG_PATH=str(config_path),TENANT_ID='mermaid',TENANT_ACCOUNT_ALLOWLIST_REQUIRED='true',ANTHROPIC_API_KEY='offline-dummy-key')
    return CatalogStore(path),assets


def fixture_auth():
    # Execute the exact host auth function, without importing startup code that
    # might read/create a real session token or initialize external adapters.
    from fastapi import Header, HTTPException
    source=ast.parse((ROOT/'wtyj/dashboard/api.py').read_text())
    function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='_check_auth')
    module=ast.Module(body=[function],type_ignores=[])
    namespace={'Header':Header,'HTTPException':HTTPException,'_SESSION_TOKEN':'isluno-local-fixture-token'}
    exec(compile(module,'dashboard/api.py','exec'),namespace)
    return namespace['_check_auth']


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True);parser.add_argument('--port',type=int,default=8787);args=parser.parse_args()
    # Deny outbound dispatch before importing runtime modules. Loopback inbound
    # listening/accepting remains available for the real browser/API caller.
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.socket.sendto=denied;socket.create_connection=denied
    store,assets=build_fixture(args.directory)
    from fastapi import FastAPI
    from dashboard.isluno_catalog_api import build_router
    from shared.isluno_media import MediaLibrary
    from shared import isluno_config
    from fastapi import Depends, Response
    app=FastAPI();auth=fixture_auth()
    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request);response.headers['X-Unboks-Tenant']='mermaid';response.headers['Cache-Control']='no-store';return response
    @app.get('/api/mermaid/dashboard/api/isluno/capabilities',dependencies=[Depends(auth)])
    def capabilities():return isluno_config.capabilities()
    app.include_router(build_router(auth,lambda:store,lambda:MediaLibrary(store.path,assets)),prefix='/api/mermaid/dashboard/api/isluno')
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port)


if __name__=='__main__': main()
