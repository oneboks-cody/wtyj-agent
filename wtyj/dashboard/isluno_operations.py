"""Read-only operator projections over tenant-owned immutable journey records."""
import json
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from agents.social.isluno_conversation import ConversationStore
from shared import config_loader, isluno_config
from shared.isluno_pricing import ItineraryError


def decode(value):
    return json.loads(value)


class Operations:
    def __init__(self, conversation=None):
        self.conversation=conversation or ConversationStore()

    def authorized(self):
        isluno_config.active_profile()
        return config_loader.get_raw()['channel_account_allowlist']['zernio_accounts'][0]

    def scope(self, row):
        scope=isluno_config.JourneyScope(**decode(row['scope_json']))
        isluno_config.require_scope(scope)
        if scope.key != row['scope_key']:
            raise isluno_config.IslunoUnavailable('Invalid stored scope')
        return scope

    def rows(self, db, scope_key=None):
        account=self.authorized()
        return db.execute('''SELECT * FROM isluno_itineraries WHERE
            json_extract(scope_json,'$.tenant_slug')=? AND json_extract(scope_json,'$.account_id')=?
            AND json_extract(scope_json,'$.journey_type')=? AND json_extract(scope_json,'$.schema_version')=?
            AND (? IS NULL OR scope_key=?) ORDER BY json_extract(payload_json,'$.updated_at') DESC,itinerary_id''',
            (isluno_config.TENANT,account,isluno_config.JOURNEY,isluno_config.SCHEMA_VERSION,scope_key,scope_key)).fetchall()

    def summary(self, db, row):
        scope=self.scope(row); itinerary=decode(row['payload_json'])
        quotes=db.execute("SELECT v.snapshot,s.status,s.summary_confirmed_at,s.approved_at FROM isluno_quote_versions v JOIN isluno_quote_state s ON s.quote_id=v.id WHERE v.scope_key=? AND json_extract(v.snapshot,'$.itinerary.id')=? ORDER BY v.version DESC",(scope.key,itinerary['id'])).fetchall()
        paid=db.execute('SELECT * FROM isluno_payments WHERE scope_key=? AND itinerary_id=?',(scope.key,itinerary['id'])).fetchone()
        session_row=db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?',(scope.key,)).fetchone()
        session=decode(session_row[0]) if session_row else {}
        snapshot=decode(paid['snapshot']) if paid else decode(quotes[0]['snapshot']) if quotes else {}
        # The current guest intake cannot rename a different historical journey.
        details=session if session.get('active_itinerary_id')==itinerary['id'] and not paid else snapshot
        guest=details.get('guest',snapshot.get('guest',{}))
        stage='demo_paid' if paid else 'cancelled' if itinerary['status']=='cancelled' else quotes[0]['status'] if quotes else 'draft'
        if stage=='superseded': stage='draft'
        quote_valid=None
        if quotes and not paid and itinerary['status']!='cancelled':
            from agents.social.isluno_quotes import QuoteStore
            try:
                QuoteStore(self.conversation)._valid(db,scope,decode(quotes[0]['snapshot'])['id'])
                quote_valid=True
            except ItineraryError:
                quote_valid=False;stage='needs_review'
        labels={'draft':'Collecting trip details','summary':'Awaiting summary confirmation','quote':'Awaiting quote approval','approved':'Awaiting demo payment','demo_paid':'Demo paid','cancelled':'Cancelled','needs_review':'Quote needs review'}
        valid={'open_inbox':True,'advance_booking':False,'real_booking':False}
        return {'id':scope.key+'.'+itinerary['id'],'guest_id':scope.key,'itinerary_id':itinerary['id'],
            'brand':'Isluno','guest_name':guest.get('name') or 'Guest name not recorded','customer_ref':scope.customer_ref,
            'conversation_id':scope.conversation_id,'stage':stage,'stage_label':labels.get(stage,'Review required'),
            'item_count':len(itinerary['items']),'totals':itinerary['totals'],'revision':itinerary['revision'],
            'created_at':itinerary['created_at'],'updated_at':itinerary['updated_at'],'valid_actions':valid,
            'inbox_path':'/conversations?c='+quote(scope.conversation_id,safe=''),
            'real_money_charged':False,'supplier_booking_made':False,'latest_quote_valid':quote_valid,
            'reference_codes':[decode(q['snapshot'])['id'] for q in quotes]+([paid['id']] if paid else []),
            'product_names':[item['product']['name'] for item in itinerary['items']],
            'guest':guest,'item_details':details.get('item_details',snapshot.get('item_details',{}))}

    def list(self, query='', offset=0, limit=50, scope_key=None):
        self.authorized()
        with self.conversation.db() as db:
            db.execute('BEGIN')
            items=[self.summary(db,row) for row in self.rows(db,scope_key)]
            query=query.casefold().strip()
            if query: items=[item for item in items if query in ' '.join(str(item[k]) for k in ('guest_name','customer_ref','itinerary_id','id','reference_codes','product_names')).casefold()]
            return {'items':items[offset:offset+limit],'total':len(items),'next_offset':offset+limit if offset+limit<len(items) else None}

    def guests(self, query='', offset=0, limit=50):
        self.authorized()
        with self.conversation.db() as db:
            db.execute('BEGIN')
            grouped={}
            for row in self.rows(db):
                item=self.summary(db,row)
                guest=grouped.setdefault(item['guest_id'],{'id':item['guest_id'],'customer_ref':item['customer_ref'],'names':[],
                    'itinerary_count':0,'item_count':0,'updated_at':item['updated_at'],'inbox_path':item['inbox_path']})
                guest['itinerary_count']+=1;guest['item_count']+=item['item_count']
                if item['guest_name'] not in guest['names']:guest['names'].append(item['guest_name'])
            guests=list(grouped.values());query=query.casefold().strip()
            if query:guests=[g for g in guests if query in (' '.join(g['names'])+' '+g['customer_ref']).casefold()]
            return {'items':guests[offset:offset+limit],'total':len(guests),'next_offset':offset+limit if offset+limit<len(guests) else None}

    def detail(self, identifier):
        self.authorized()
        if not re.fullmatch(r'[a-f0-9]{64}\.[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}',identifier):
            raise HTTPException(404,'Itinerary unavailable')
        scope_key,itinerary_id=identifier.split('.',1)
        with self.conversation.db() as db:
            db.execute('BEGIN')
            rows=self.rows(db,scope_key)
            row=next((r for r in rows if r['itinerary_id']==itinerary_id),None)
            if row is None: raise HTTPException(404,'Itinerary unavailable')
            scope=self.scope(row); result=self.summary(db,row);itinerary=decode(row['payload_json'])
            quotes=[];events=[];documents=[]
            for v in db.execute('''SELECT v.*,s.status,s.summary_confirmed_at,s.approved_at FROM isluno_quote_versions v
                    JOIN isluno_quote_state s ON s.quote_id=v.id WHERE v.scope_key=? AND json_extract(v.snapshot,'$.itinerary.id')=? ORDER BY v.version''',(scope.key,itinerary_id)):
                snapshot=decode(v['snapshot'])
                quotes.append({'id':v['id'],'version':v['version'],'status':v['status'],'snapshot':snapshot,
                               'summary_confirmed_at':v['summary_confirmed_at'],'approved_at':v['approved_at'],'expires_at':v['expires_at'],'sha256':v['pdf_sha256']})
                documents.append({'id':v['id'],'kind':'quote','item_id':None,'quote_version':v['version'],'sha256':v['pdf_sha256']})
                for label,key in [('Quote prepared','created_at'),('Summary confirmed','summary_confirmed_at'),('Quote approved','approved_at')]:
                    at=snapshot.get(key) if key=='created_at' else v[key]
                    if at:events.append({'kind':label,'at':at,'reference':v['id'],'version':v['version']})
            paid=db.execute('SELECT * FROM isluno_payments WHERE scope_key=? AND itinerary_id=?',(scope.key,itinerary_id)).fetchone()
            payment=None
            if paid:
                snapshot=decode(paid['snapshot']);payment={'id':paid['id'],'quote_id':paid['quote_id'],'paid_at':snapshot['paid_at'],'mode':'simulated','real_money_charged':False,'supplier_booking_made':False,
                    'emails':[dict(e) for e in db.execute('SELECT id,recipient,status,message_id,error FROM isluno_paid_emails WHERE payment_id=?',(paid['id'],))]}
                documents += [dict(d) for d in db.execute('SELECT id,kind,item_id,ticket_id,sha256,expires_at FROM isluno_paid_documents WHERE payment_id=? ORDER BY rowid',(paid['id'],))]
                events.append({'kind':'Demo payment completed','at':snapshot['paid_at'],'reference':paid['id']})
            quote_by_id={q['id']:q for q in quotes};document_by_id={d['id']:d for d in documents};deliveries=[]
            for j in db.execute('SELECT * FROM isluno_quote_jobs WHERE scope_key=? ORDER BY rowid',(scope.key,)):
                if j['quote_id'] not in quote_by_id:continue
                quote_record=quote_by_id[j['quote_id']]
                job=decode(j['payload'])
                for d in db.execute('SELECT * FROM isluno_quote_deliveries WHERE job_id=? ORDER BY part',(j['id'],)):
                    body=job['parts'][d['part']]
                    document=document_by_id.get(body.get('document_quote_id'),{})
                    deliveries.append({'quote_id':j['quote_id'],'quote_version':quote_record['version'],'quote_status':quote_record['status'],
                        'document_kind':document.get('kind'),'item_id':document.get('item_id'),'ticket_id':document.get('ticket_id'),'part_count':len(job['parts']),
                        'job_id':j['id'],'part':d['part'],'stage':job['stage'],'status':d['status'],'provider_id':d['provider_id'],
                        'document_id':body.get('document_quote_id'),'label':body.get('attachmentName') or 'WhatsApp '+job['stage'].replace('_',' '),'delivered_at':None})
            versions=[decode(v[0]) for v in db.execute('SELECT payload_json FROM isluno_itinerary_versions WHERE scope_key=? AND itinerary_id=? ORDER BY revision',(scope.key,itinerary_id))]
            for version in versions:events.append({'kind':'Itinerary revision','at':version['updated_at'],'revision':version['revision'],'status':version['status']})
            requests=[dict(r) for r in db.execute('SELECT id,reason,status FROM isluno_operator_requests WHERE scope_key=? AND (itinerary_id=? OR itinerary_id IS NULL)',(scope.key,itinerary_id))]
            item_stages={}
            for item in itinerary['items']:
                ticket=next((d for d in documents if d['kind']=='ticket' and d.get('item_id')==item['id']),None)
                item_stages[item['id']]={'stage':'demo_paid' if paid and ticket else 'cancelled' if itinerary['status']=='cancelled' else 'demo_selected',
                    'label':'Demo paid · ticket issued' if paid and ticket else 'Itinerary cancelled' if itinerary['status']=='cancelled' else 'Demo selection · no supplier reservation'}
            if paid:
                for action in db.execute('SELECT trigger_id FROM isluno_payment_events WHERE scope_key=? AND payment_id=?',(scope.key,paid['id'])):
                    events.append({'kind':'Verified payment action recorded','at':None,'reference':action['trigger_id']})
            result.update({'item_stages':item_stages,'itinerary':itinerary,'versions':versions,'quotes':quotes,'payment':payment,'documents':documents,'deliveries':deliveries,
                'events':sorted(events,key=lambda e:e['at'] or '9999'),'operator_requests':requests,'related_itineraries':[self.summary(db,r) for r in rows if r['itinerary_id']!=itinerary_id],
                'delivery_notice':'Accepted means provider acceptance, not guest delivery. Send timestamps are unavailable in the current ledger.'})
            return result

    def document(self, identifier, document_id):
        detail=self.detail(identifier)
        if not any(d['id']==document_id for d in detail['documents']):raise HTTPException(404,'Document unavailable')
        with self.conversation.db() as db:
            row=db.execute('SELECT pdf FROM isluno_quote_versions WHERE id=?',(document_id,)).fetchone()
            if row is None:row=db.execute('SELECT pdf FROM isluno_paid_documents WHERE id=?',(document_id,)).fetchone()
            if row is None:raise HTTPException(404,'Document unavailable')
            return bytes(row[0])


def build_router(check_auth, factory=Operations):
    router=APIRouter(prefix='/operations',dependencies=[Depends(check_auth)])
    def call(method,*args):
        try:return getattr(factory(),method)(*args)
        except isluno_config.IslunoUnavailable as exc:raise HTTPException(403,'Isluno operations unavailable') from exc
        except ItineraryError as exc:raise HTTPException(400,exc.code) from exc
    @router.get('/guests')
    def guests(response:Response,q:str=Query(default='',max_length=200),offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
        response.headers['Cache-Control']='no-store';return call('guests',q,offset,limit)
    @router.get('/journeys')
    def journeys(response:Response,q:str=Query(default='',max_length=200),offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100),guest_id:str|None=Query(default=None,pattern='^[a-f0-9]{64}$')):
        response.headers['Cache-Control']='no-store';return call('list',q,offset,limit,guest_id)
    @router.get('/journeys/{identifier}')
    def journey(response:Response,identifier:str):
        response.headers['Cache-Control']='no-store';return call('detail',identifier)
    @router.get('/journeys/{identifier}/documents/{document_id}.pdf')
    def document(identifier:str,document_id:str):
        return Response(call('document',identifier,document_id),media_type='application/pdf',headers={'Cache-Control':'no-store','Content-Disposition':'inline; filename="isluno-document.pdf"','X-Content-Type-Options':'nosniff'})
    return router
