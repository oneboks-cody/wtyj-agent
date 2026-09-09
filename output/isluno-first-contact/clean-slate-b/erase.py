"""One authorized tenant-local history erasure. Caller must stop all writers."""
import sqlite3, json, hashlib, datetime

def erase(path, scope):
    db=sqlite3.connect(str(path),timeout=0)
    q=lambda s:'"'+s.replace('"','""')+'"'
    try:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('BEGIN EXCLUSIVE')
        tables=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        lazy={'isluno_contexts','isluno_discovery_plans','isluno_discovery_actions'}
        assert set(tables)<=set(scope['tables'])|lazy, 'unknown_table'
        for name,sql in tables.items():
            if name in scope['tables']:
                assert hashlib.sha256(sql.encode()).hexdigest()==scope['tables'][name], 'schema_changed'
        assert db.execute("SELECT generation,phase FROM mermaid_maintenance WHERE tenant='mermaid'").fetchall()==[(15,'draining')]
        assert db.execute("SELECT count(*) FROM mermaid_maintenance_workers WHERE status!='finished'").fetchone()[0]==0,'unfinished_workers'
        assert db.execute("SELECT tenant FROM isluno_cutover").fetchall()==[('mermaid',)]
        assert db.execute('SELECT count(*) FROM customers').fetchone()[0]<=1,'unexpected_customer_scope'
        schema=list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        preserve={t:list(db.execute('SELECT * FROM '+q(t)+' ORDER BY rowid')) for t in scope['preserve_tables']}
        delete=set(scope['delete_tables'])|(set(tables)&lazy)
        before={t:db.execute('SELECT count(*) FROM '+q(t)).fetchone()[0] for t in delete}
        assert sum(before.values())<=100000,'row_bound'
        ids={r[0] for r in db.execute('SELECT message_id FROM inbound_processing_events UNION SELECT message_id FROM whatsapp_processed')}
        if 'zernio_failed_event_queue' in tables:
            ids.update(r[0] for r in db.execute("SELECT message_id FROM zernio_failed_event_queue WHERE message_id IS NOT NULL AND message_id!=''"))
        assert len(ids)<=100000 and all(isinstance(v,str) and 0<len(v)<=2048 for v in ids)
        triggers=list(db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"))
        reviewed={r['name']:r['sql'] for r in scope['triggers']}
        dropped=[]
        for name,table,sql in triggers:
            if table in delete:
                assert reviewed.get(name)==sql, 'unknown_trigger'
                db.execute('DROP TRIGGER '+q(name));dropped.append(sql)
        for t in sorted(delete):db.execute('DELETE FROM '+q(t))
        now=datetime.datetime.now(datetime.timezone.utc).isoformat()
        db.execute('DELETE FROM inbound_processing_events')
        db.executemany("INSERT INTO inbound_processing_events(message_id,status,reason,created_at,updated_at) VALUES(?,'ignored','history_cleared_no_replay',?,?)",((v,now,now) for v in ids))
        db.execute('UPDATE whatsapp_processed SET created_at=?',(now,))
        for sql in dropped:db.execute(sql)
        assert schema==list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")),'schema_not_preserved'
        for t,rows in preserve.items():assert rows==list(db.execute('SELECT * FROM '+q(t)+' ORDER BY rowid')),'preserved_table_changed'
        assert not list(db.execute('PRAGMA foreign_key_check')),'foreign_key_check'
        assert all(db.execute('SELECT count(*) FROM '+q(t)).fetchone()[0]==0 for t in delete)
        db.commit()
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]==0
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]==0
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        return {'deleted_rows':{t:c for t,c in before.items() if c},'empty_tables':sorted(delete),'payload_free_tombstones':len(ids),'schema_preserved':True,'business_tables_preserved':True,'backup_created':False}
    except BaseException:
        db.rollback();raise
    finally:db.close()
