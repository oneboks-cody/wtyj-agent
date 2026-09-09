import json,sqlite3
from pathlib import Path
from shared import config_loader,isluno_config,mermaid_maintenance as m
from shared.isluno_catalog import CatalogStore
from dashboard.isluno_operations import Operations
caps=isluno_config.capabilities();assert caps['enabled'] and caps['tenant_slug']=='mermaid' and all(caps['capabilities'].values())
raw=config_loader.get_raw();assert raw['features']['mermaid_reminders'] is False and raw['isluno']['native_carousels'] is False
snapshot=CatalogStore(Path(config_loader._CONFIG_PATH).with_name('isluno_catalog.json')).snapshot();assert len(snapshot['catalog']['products'])==31
ops=Operations();today=ops.today();assert all(n==0 for n in today['counts'].values());assert ops.list()['total']==0 and ops.guests()['total']==0
path='/app/data/state_registry.db';db=sqlite3.connect('file:'+path+'?mode=ro',uri=True)
try:
 for table in DELETE_TABLES:
  if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():assert db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0]==0
 assert db.execute("SELECT count(*) FROM inbound_processing_events WHERE status!='ignored' OR reason!='history_cleared_no_replay' OR payload_json!='{}' OR conversation_id!='' OR processing_token!=''").fetchone()[0]==0
finally:db.close()
s=m.status(path);assert s['phase']=='sealed' and s['generation']==15 and not s['workers'] and not s['pending'] and not s['unreviewed_ledgers']
print(json.dumps({'empty':True,'today_counts':today['counts'],'guests':0,'journeys':0,'catalog_products':31,'capabilities_preserved':True,'reminders_enabled':False}))
