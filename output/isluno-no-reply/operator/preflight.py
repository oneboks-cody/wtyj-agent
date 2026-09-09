# Before staging/build/disruption: reuse read-only base-image coordinator status.
mm=inspect('wtyj-mermaid')
require(mm['Image']==BACKEND and mm['Id']=='b5ae7a2601fdff630a4e8e2f32e9a97f7576e5c00e27290ccafd6e2da7e6735c' and mm['State']['Running'],'preflight_runtime')
code=OPERATOR_CODE+"\ndb=sqlite3.connect('file:/app/data/state_registry.db?mode=ro',uri=True,timeout=0)\ntry:\n db.execute('PRAGMA query_only=ON');db.execute('BEGIN')\n s=maintenance._snapshot(db);check(s['phase']=='open' and s['generation']==4,'preflight_generation')\n proof=verify(db,"+repr(TEST_HASH)+","+repr(PROOF_HASH)+")\n print(json.dumps({'status':'preflight_pass','proof':proof}))\nfinally:db.close()\n"
print(cmd(['docker','exec',mm['Id'],'python','-c',code],10))
