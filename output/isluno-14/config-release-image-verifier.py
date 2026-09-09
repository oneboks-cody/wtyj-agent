import sys,os,json,hashlib,sqlite3,contextlib,io
from pathlib import Path
sys.path.insert(0,'/release-tools/wtyj/scripts')
import apply_isluno_release_config as m
real_render=m.render
m.render=lambda doc,phase,generation=None:real_render(doc,phase,generation,hashlib.sha256(b'synthetic-account').hexdigest())
p=Path('/app/config/client.json')
p.write_text(json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'credentials':{'secret':'synthetic-only'},'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic-account']}}));p.chmod(0o600)
Path('/release-backup/config').mkdir(mode=0o700)
os.environ['CLIENT_CONFIG_PATH']=str(p)

def run(phase,gen=None):
    args=['--execute-approved-write','--phase',phase,'--expected-sha256',hashlib.sha256(p.read_bytes()).hexdigest(),'--service-stopped-verified']
    if gen is not None:args+=['--sealed-generation',str(gen)]
    with contextlib.redirect_stdout(io.StringIO()) as out:code=m.main(args)
    result=json.loads(out.getvalue());assert 'synthetic' not in out.getvalue()
    return code,result
assert run('stage')[0]==0
from shared import config_loader
config_loader._CONFIG_PATH=str(p);config_loader._invalidate_cache()
from shared import mermaid_maintenance as mm
from project_isluno_old_work import TABLES
with sqlite3.connect('/app/data/state_registry.db') as db:
    for name,cols in TABLES.items():db.execute('CREATE TABLE '+name+' ('+','.join(c+(' INTEGER' if c in ('attempts','expires_at') else ' TEXT') for c in cols)+')')
mm.initialize(db_path='/app/data/state_registry.db')
gen=mm.close(0,seconds=120,coverage=mm.COVERAGE,evidence='synthetic',db_path='/app/data/state_registry.db')
mm.seal(gen,db_path='/app/data/state_registry.db')
before=p.read_bytes()
config_loader._CONFIG_PATH='/app/config/wrong.json'
assert run('activate',gen)[0]==1;assert p.read_bytes()==before
config_loader._CONFIG_PATH=str(p);config_loader._invalidate_cache()
assert run('activate',gen+1)[0]==1;assert p.read_bytes()==before
code,result=run('activate',gen);assert code==0
assert result['config_sha256']==hashlib.sha256(p.read_bytes()).hexdigest()
doc=json.loads(p.read_bytes());assert doc['features']['isluno_itinerary_demo_v1'] is True
assert doc['credentials']['secret']=='synthetic-only'
assert run('rollback')[0]==0
assert json.loads(p.read_bytes())['features']['isluno_itinerary_demo_v1'] is False
assert len(list(Path('/release-backup/config').iterdir()))==3
assert 'shared.state_registry' not in sys.modules
print(json.dumps({'status':'pass','actual_cli_phases':['stage','activate','rollback'],'wrong_generation_rejected':True,'private_backups':3,'state_registry_imported':False,'network':'none','source_mount':'/release-tools/wtyj/scripts'}))
