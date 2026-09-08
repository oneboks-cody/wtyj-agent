"""Loopback operations fixture seeded through actual synthetic conversation actions."""
import argparse
import ast
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'wtyj'),str(ROOT/'wtyj/tests/isluno')]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8788);parser.add_argument('--manifest',required=True);parser.add_argument('--workspace',action='store_true');args=parser.parse_args()
    from scripts.serve_isluno_catalog_fixture import denied,fixture_auth
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.socket.sendto=denied;socket.create_connection=denied
    os.environ['ANTHROPIC_API_KEY']='offline-dummy-key'
    # Import-time loader sees only a synthetic, disposable config.
    import tempfile
    initial=tempfile.TemporaryDirectory(prefix='isluno-ops-bootstrap-');bootstrap=Path(initial.name)/'client.json';bootstrap.write_text(json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'features':{}}));os.environ['CLIENT_CONFIG_PATH']=str(bootstrap)
    from test_payments import PaymentTests
    from test_conversation import response
    from shared import isluno_config
    fixture=PaymentTests('test_actual_payment_receipt_and_unique_ticket_per_item');fixture.setUp()
    fixture.scope=lambda:isluno_config.verified_scope(account_id='synthetic-account',conversation_id='fixture-guest-one',customer_ref='fixture-whatsapp-one')
    fixture.initial()
    fixture.scope=lambda:isluno_config.verified_scope(account_id='synthetic-account',conversation_id='fixture-guest-two',customer_ref='fixture-whatsapp-two')
    fixture.initial();fixture.turn(response('add',[{'product_id':'fixture-cruise','date':'2026-10-16'}],{'name':'Second Party'}))
    result,_=fixture.turn(response('summary'));summary=fixture.job(result);fixture.send_quote(summary['id'])
    quote=fixture.tap(summary);fixture.send_quote(quote['id'],['ambiguous'])
    result,_=fixture.turn(response('update',[{'item_id':fixture.active()['items'][0]['id'],'date':'2026-10-19'}]));summary=fixture.job(result)
    fixture.send_quote(summary['id']);quote=fixture.tap(summary);fixture.send_quote(quote['id']);approved=fixture.tap(quote);fixture.send_quote(approved['id']);job=fixture.pay(approved)
    fixture.deliver(job,['accepted','accepted','ambiguous'])
    fixture.scope=lambda:isluno_config.verified_scope(account_id='synthetic-account',conversation_id='fixture-guest-three',customer_ref='fixture-whatsapp-three')
    job=fixture.pay();fixture.deliver(job)
    from dashboard.isluno_operations import Operations,build_router
    from fastapi import FastAPI,Depends
    import uvicorn
    store=Operations(fixture.store);app=FastAPI();auth=fixture_auth()
    @app.middleware('http')
    async def headers(request,call_next):
        result=await call_next(request);result.headers['X-Unboks-Tenant']='ali-car-rental' if request.url.path.startswith('/api/ali-car-rental/') else 'mermaid';result.headers['Cache-Control']='no-store';return result
    @app.get('/api/mermaid/dashboard/api/isluno/capabilities',dependencies=[Depends(auth)])
    def capabilities():return isluno_config.capabilities()
    app.include_router(build_router(auth,lambda:store),prefix='/api/mermaid/dashboard/api/isluno')
    if args.workspace:
        from scripts.isluno_workspace_fixture import install
        install(app,auth,fixture)
        from dashboard.isluno_catalog_api import build_router as catalog_router
        app.include_router(catalog_router(auth,lambda:__import__('shared.isluno_catalog',fromlist=['CatalogStore']).CatalogStore(fixture.itinerary.catalog_path)),prefix='/api/mermaid/dashboard/api/isluno')
    Path(args.manifest).parent.mkdir(parents=True,exist_ok=True)
    Path(args.manifest).write_text(json.dumps({'directory':str(fixture.directory),'journeys':store.list(),'fixture_only':True},indent=2))
    try:uvicorn.run(app,host='127.0.0.1',port=args.port)
    finally:fixture.doCleanups();initial.cleanup()

if __name__=='__main__':main()
