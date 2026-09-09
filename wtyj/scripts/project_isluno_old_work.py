"""Fixed aggregate of a private, detached post-quiescence SQLite backup. Default offline."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from inspect_isluno_target import safe_open, require

MAX_BYTES=256*1024*1024
MAX_ROWS=100000
TABLES={
 'inbound_processing_events':('message_id','channel','status','reason','last_error','processing_token','lease_expires_at','outbound_attempted_at'),
 'whatsapp_processed':('message_id',),
 'mermaid_reservations':('public_id','tenant_slug','state','payment_reference'),
 'mermaid_demo_payments':('reservation_public_id','tenant_slug','status'),
 'mermaid_checkout_links':('tenant_slug','reservation_public_id','expires_at'),
 'mermaid_abandoned_reminders':('tenant','status'),
 'mermaid_delivery_jobs':('tenant_slug','status','attempts','last_error'),
 'mermaid_reservation_emails':('reservation_public_id','status','error_code'),
 'mermaid_date_changes':('reservation_public_id','status','delivery_status'),
 'mermaid_email_preferences':('reservation_public_id','phase'),
 'mermaid_documents':('public_id','tenant_slug','reservation_public_id'),
 'mermaid_card_deliveries':('document_public_id','status','provider_message_id'),
}
SAFE_INBOUND={('replied','provider_send_ok'),('ignored','ignored_contact'),('ignored','ignored_phone'),
 ('ignored','blocked_conversation'),('ignored','non_text_message'),('escalated','human_takeover_ai_muted')}
BUCKETS=('settled_record','customer_waiting','unfinished','uncertain','unknown')


def classify(table,row):
    # Every tuple contains selected status fields/booleans only, never payloads.
    if table=='inbound_processing_events':
        channel,status,reason,error,token,lease,attempt,dedup=row
        if channel!='whatsapp' or not dedup or any(type(v) is not int or v not in (0,1) for v in (error,token,lease,attempt,dedup)):return 'unknown'
        if (status,reason) in SAFE_INBOUND and not(error or token or lease):return 'settled_record'
        if attempt:return 'uncertain'
        if status in ('received','processing','recovering','paused','processing_failed','send_failed','superseded'):return 'unfinished'
        return 'unknown'
    if table=='mermaid_reservations':
        tenant,state,payment_ref,payment=row
        if tenant!='mermaid':return 'unknown'
        if state=='booked':return 'settled_record' if payment_ref and payment else 'unknown'
        if state=='cancelled':return 'unknown' if payment_ref or payment else 'settled_record'
        if state in ('demo_availability_approved','quote_ready','demo_payment_pending') and not(payment_ref or payment):return 'customer_waiting'
        if state=='demo_paid' or payment_ref or payment:return 'uncertain'
        return 'unknown'
    if table=='mermaid_demo_payments':
        tenant,status,parent_booked=row
        return 'settled_record' if tenant=='mermaid' and status=='simulated_success' and parent_booked else 'unknown'
    if table=='mermaid_checkout_links':
        tenant,parent_known,expires=row
        return 'customer_waiting' if tenant=='mermaid' and parent_known and type(expires) is int else 'unknown'
    if table=='mermaid_abandoned_reminders':
        tenant,status=row
        if tenant!='mermaid':return 'unknown'
        if status in ('sent','cancelled','skipped_window'):return 'settled_record'
        if status in ('sending','uncertain','claimed','failed'):return 'uncertain'
        return 'unknown'
    if table=='mermaid_delivery_jobs':
        tenant,status,attempts,error=row
        if tenant!='mermaid' or type(attempts) is not int or attempts<0:return 'unknown'
        if status=='delivered' and attempts>0 and not error:return 'settled_record'
        if attempts>0 or status in ('sending','uncertain','claimed','ambiguous'):return 'uncertain'
        if status=='pending':return 'unfinished'
        return 'unknown'
    if table=='mermaid_card_deliveries':
        status,provider_id,parent=row
        if not parent:return 'unknown'
        if status=='delivered' and provider_id:return 'settled_record'
        if status in ('prepared','pending','failed') or provider_id:return 'uncertain'
        return 'unknown'
    if table=='mermaid_reservation_emails':
        status,error,parent=row
        if not parent:return 'unknown'
        if status=='accepted' and not error:return 'settled_record'  # SMTP acceptance, not inbox delivery.
        if status in ('sending','uncertain','failed'):return 'uncertain'
        return 'unknown'
    if table=='mermaid_date_changes':
        status,delivery,parent=row
        if not parent:return 'unknown'
        if status in ('cancelled','superseded'):return 'settled_record'
        if status=='pending':return 'customer_waiting'
        if status=='confirmed':return 'settled_record' if delivery=='delivered' else 'unfinished'
        return 'unknown'
    if table=='mermaid_email_preferences':
        phase,parent=row
        if not parent:return 'unknown'
        if phase in ('sent','declined'):return 'settled_record'
        if phase in ('offered','awaiting_confirmation','awaiting_date_confirmation','awaiting_address'):return 'customer_waiting'
        if phase=='consented':return 'unfinished'
        return 'unknown'
    raise ValueError()


QUERIES={
 'inbound_processing_events':"SELECT substr(channel,1,64),substr(status,1,64),substr(reason,1,128),CASE WHEN typeof(last_error)='text' AND last_error='' THEN 0 ELSE 1 END,CASE WHEN typeof(processing_token)='text' AND processing_token='' THEN 0 ELSE 1 END,CASE WHEN typeof(lease_expires_at)='text' AND lease_expires_at='' THEN 0 ELSE 1 END,CASE WHEN typeof(outbound_attempted_at)='text' AND outbound_attempted_at='' THEN 0 ELSE 1 END,EXISTS(SELECT 1 FROM whatsapp_processed w WHERE w.message_id=i.message_id) FROM inbound_processing_events i",
 'mermaid_reservations':"SELECT substr(tenant_slug,1,64),substr(state,1,64),length(coalesce(payment_reference,''))>0,EXISTS(SELECT 1 FROM mermaid_demo_payments p WHERE p.reservation_public_id=r.public_id AND p.tenant_slug='mermaid' AND p.status='simulated_success') FROM mermaid_reservations r",
 'mermaid_demo_payments':"SELECT substr(tenant_slug,1,64),substr(status,1,64),EXISTS(SELECT 1 FROM mermaid_reservations r WHERE r.public_id=p.reservation_public_id AND r.tenant_slug='mermaid' AND r.state='booked') FROM mermaid_demo_payments p",
 'mermaid_checkout_links':"SELECT substr(tenant_slug,1,64),EXISTS(SELECT 1 FROM mermaid_reservations r WHERE r.public_id=c.reservation_public_id AND r.tenant_slug='mermaid'),expires_at FROM mermaid_checkout_links c",
 'mermaid_abandoned_reminders':"SELECT substr(tenant,1,64),substr(status,1,64) FROM mermaid_abandoned_reminders",
 'mermaid_delivery_jobs':"SELECT substr(tenant_slug,1,64),substr(status,1,64),attempts,length(coalesce(last_error,''))>0 FROM mermaid_delivery_jobs",
 'mermaid_reservation_emails':"SELECT substr(status,1,64),length(coalesce(error_code,''))>0,EXISTS(SELECT 1 FROM mermaid_reservations r WHERE r.public_id=e.reservation_public_id AND r.tenant_slug='mermaid') FROM mermaid_reservation_emails e",
 'mermaid_date_changes':"SELECT substr(status,1,64),substr(delivery_status,1,64),EXISTS(SELECT 1 FROM mermaid_reservations r WHERE r.public_id=d.reservation_public_id AND r.tenant_slug='mermaid') FROM mermaid_date_changes d",
 'mermaid_card_deliveries':"SELECT substr(status,1,64),CASE WHEN typeof(provider_message_id)='text' AND length(provider_message_id)>0 THEN 1 ELSE 0 END,EXISTS(SELECT 1 FROM mermaid_documents d WHERE d.public_id=c.document_public_id AND d.tenant_slug='mermaid') FROM mermaid_card_deliveries c",
 'mermaid_email_preferences':"SELECT substr(phase,1,64),EXISTS(SELECT 1 FROM mermaid_reservations r WHERE r.public_id=e.reservation_public_id AND r.tenant_slug='mermaid') FROM mermaid_email_preferences e",
}


def signature(info):return(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def project(snapshot):
    path=Path(snapshot);require(path.is_absolute())
    parent=safe_open(str(path.parent),directory=True)
    fd=None;db=None
    deadline=time.monotonic()+10
    try:
        require(stat.S_IMODE(os.fstat(parent).st_mode)==0o700 and os.fstat(parent).st_uid==os.geteuid())
        for suffix in ('-wal','-shm','-journal'):
            try:os.stat(path.name+suffix,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:continue
            raise ValueError('not_detached')
        fd=safe_open(str(path));before=os.fstat(fd)
        require(before.st_uid==os.geteuid() and stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1)
        require(0<before.st_size<=MAX_BYTES)
        digest=hashlib.sha256();size=0
        while True:
            block=os.read(fd,min(65536,MAX_BYTES-size+1))
            if not block:break
            size+=len(block);require(size<=MAX_BYTES and time.monotonic()<deadline);digest.update(block)
        require(size==before.st_size)
        descriptor_path=f'/proc/self/fd/{fd}' if sys.platform=='linux' else f'/dev/fd/{fd}'
        db=sqlite3.connect('file:'+descriptor_path+'?mode=ro&immutable=1',uri=True,timeout=0)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic()>=deadline),1000)
        db.execute('BEGIN')
        present_tables=set()
        for table,columns in TABLES.items():
            entry=db.execute("SELECT type FROM sqlite_master WHERE name=?",(table,)).fetchone()
            if entry is None:
                require(table not in ('inbound_processing_events','whatsapp_processed'))
                continue
            require(entry[0]=='table');present_tables.add(table)
            present={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
            require(set(columns)<=present)
            count=db.execute('SELECT count(*) FROM (SELECT 1 FROM '+table+' LIMIT ?)',(MAX_ROWS+1,)).fetchone()[0]
            require(count<=MAX_ROWS)
        dependencies={'mermaid_reservations':'mermaid_demo_payments','mermaid_demo_payments':'mermaid_reservations','mermaid_checkout_links':'mermaid_reservations','mermaid_reservation_emails':'mermaid_reservations','mermaid_date_changes':'mermaid_reservations','mermaid_email_preferences':'mermaid_reservations','mermaid_card_deliveries':'mermaid_documents','mermaid_documents':'mermaid_reservations'}
        for child,parent_table in dependencies.items():
            require(child not in present_tables or parent_table in present_tables)
        counts={}
        for table,query in QUERIES.items():
            if table not in present_tables:continue
            buckets=dict.fromkeys(BUCKETS,0)
            for row in db.execute(query+' LIMIT ?', (MAX_ROWS+1,)):
                buckets[classify(table,row)]+=1
            counts[table]=buckets
        require(signature(os.fstat(fd))==signature(before))
        current=safe_open(str(path))
        try:require(signature(os.fstat(current))==signature(before))
        finally:os.close(current)
        require(time.monotonic()<deadline)
        blocking=sum(v[k] for v in counts.values() for k in ('unfinished','uncertain','unknown'))
        result={'status':'projected','snapshot_sha256':digest.hexdigest(),'snapshot_bytes':size,
                'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'counts':counts,'not_initialized':sorted(set(TABLES)-present_tables),'blocking_records':blocking,'activation_authorized':False,
                'quiescence_proven_by_this_tool':False}
        require(len(json.dumps(result).encode())<=8192)
        return result
    finally:
        if db is not None:db.close()
        if fd is not None:os.close(fd)
        os.close(parent)


class FixedParser(argparse.ArgumentParser):
    def error(self,message):raise ValueError()


def main(argv=None):
    try:
        p=FixedParser(description=__doc__);p.add_argument('--approved-detached-snapshot');args=p.parse_args(argv)
        if not args.approved_detached_snapshot:
            print('{"status":"offline_default","snapshot_read":false}');return 0
        def expired(*_):raise ValueError()
        old=signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,10)
        try:result=project(args.approved_detached_snapshot)
        finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print('{"status":"stopped","check":"old_work"}');return 1


if __name__=='__main__':raise SystemExit(main())
