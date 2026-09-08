"""Synthetic shell endpoints; never installed by production routers."""
import ast
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

def install(app, auth, fixture):
    from shared import icp_overrides
    state={'mode':'unknown','writes':0}
    def envelope():
        if state['mode']=='error': raise HTTPException(503,'Synthetic status outage')
        return {'available':state['mode'] in {'active','paused','write_error'},'feature_toggles':{'ai_auto_reply':{'value':state['mode']!='paused','source':'synthetic_fixture'}}}
    def write(active):
        if state['mode']=='write_error':raise RuntimeError('Synthetic write failure')
        state['writes']+=1;state['mode']='active' if active else 'paused';return envelope()
    fixture.enterContext(__import__('unittest.mock',fromlist=['patch']).patch.object(icp_overrides,'fetch_overrides',envelope))
    fixture.enterContext(__import__('unittest.mock',fromlist=['patch']).patch.object(icp_overrides,'set_auto_reply_enabled',write))
    source=ast.parse((Path(__file__).resolve().parents[1]/'dashboard/api.py').read_text())
    selected=[node for node in source.body if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in {'AgentControlRequest','get_agent_status','set_agent_status'}]
    for node in selected:
        if isinstance(node,ast.FunctionDef):node.decorator_list=[]
    namespace={'BaseModel':BaseModel,'StrictBool':StrictBool,'HTTPException':HTTPException}
    exec(compile(ast.Module(body=selected,type_ignores=[]),'dashboard/api.py','exec'),namespace)
    router=APIRouter(prefix='/api/mermaid/dashboard/api',dependencies=[Depends(auth)])
    router.add_api_route('/agent/status',namespace['get_agent_status'],methods=['GET'])
    router.add_api_route('/agent/status',namespace['set_agent_status'],methods=['PUT'])
    @router.get('/client/profile')
    def profile():return {'slug':'mermaid','name':'Mermaid Boat Trips Curaçao','status':'unknown'}
    @router.get('/__fixture/state')
    def get_state():return state
    @router.put('/__fixture/state')
    def set_state(body:dict):
        if body.get('mode') in {'unknown','active','paused','error','write_error'}:state['mode']=body['mode']
        if 'enabled' in body:
            config=dict(fixture.config);config['features']={'isluno_itinerary_demo_v1':body['enabled'] is True};fixture.write_config(config)
        return state
    @router.get('/messages/conversations')
    @router.get('/escalations')
    @router.get('/blocked-senders')
    def empty_rows():return []
    @router.get('/mermaid-reservations')
    @router.get('/mermaid-crew-assistance')
    def legacy_rows():return {'items':[],'demo':True,'remindersEnabled':False}
    app.include_router(router)
    other=APIRouter(prefix='/api/ali-car-rental/dashboard/api',dependencies=[Depends(auth)])
    @other.get('/client/profile')
    def other_profile():return {'slug':'ali-car-rental','name':'Ali Car Rental','status':'unknown'}
    @other.get('/agent/status')
    def other_agent():return {'available':False,'active':None,'status':'unavailable','source':'synthetic_fixture','updatedAt':None}
    app.include_router(other)
