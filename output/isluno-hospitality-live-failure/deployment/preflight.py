mm=inspect('wtyj-mermaid')
require(mm['Image']==BACKEND and mm['Id']==CURRENT_CONTAINER and mm['State']['Running'],'preflight_runtime')
require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','preflight_config')
require(digest(CLIENT.with_name('isluno_profile.json'))=='5c90d1979c7599e9bdd82a809a6cebe07db150692ed949b7a88e1070b16616b3','preflight_profile')
code=OPERATOR_CODE+"\ndb=sqlite3.connect('file:/app/data/state_registry.db?mode=ro',uri=True,timeout=0)\ntry:\n db.execute('PRAGMA query_only=ON');db.execute('BEGIN');s=maintenance._snapshot(db);check(s['phase']=='open' and s['generation']==8,'preflight_generation');proof=verify(db,"+repr(PROOF_HASH)+");print(json.dumps({'status':'preflight_pass','proof':proof}))\nfinally:db.close()\n"
print(cmd(['docker','exec',mm['Id'],'python','-c',code],10))
