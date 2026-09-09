from pathlib import Path
import json,sys
sys.path.insert(0,str(Path('wtyj/scripts').resolve()))
from inspect_isluno_target import bounded_command,ssh_argv
common=Path('wtyj/scripts/inspect_isluno_target.py').read_text().split('\ndef fields(')[0]
operator=Path('tmp/isluno-presentation-rollout/retained_history.py').read_text()
inner=operator+"\ndb=sqlite3.connect('file:/app/data/state_registry.db?mode=ro',uri=True,timeout=0)\ntry:\n db.execute('PRAGMA query_only=ON');db.execute('BEGIN');print(json.dumps(review(db)))\nexcept Exception as exc:print(json.dumps({'status':'stopped','code':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}))\nfinally:db.close()\n"
payload=common+'\nsignal.alarm(15)\nprint(bounded_command('+repr(['docker','exec','1aa2e6946220f25f3130746f2cff5bf71412e320d1ec03078202e876640685b2','python','-c',inner])+',timeout=10,cap=3000))'
out=bounded_command(ssh_argv(),timeout=20,cap=3500,data=payload.encode())
Path('tmp/isluno-presentation-rollout/post-deployment-proof.json').write_text(out);print(out)
