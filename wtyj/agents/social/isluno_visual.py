"""Ordinary image recommendations in one durable plan; no provider calls."""
import json
from agents.social.isluno_wire import messages, text_of, units, MAX_PARTS
from agents.social import isluno_understanding
from shared.isluno_media import MediaUnavailable
from shared.isluno_pricing import check

# Native UI labels, not generated conversational prose or business claims.
ACTIONS={
 'en':('More photos','Trip details','Plan this trip'),
 'nl':('Meer foto’s','Reisdetails','Plan deze reis'),
 'de':('Mehr Fotos','Reisedetails','Reise planen'),
 'es':('Más fotos','Detalles del viaje','Planear este viaje'),
 'pt':('Mais fotos','Detalhes da viagem','Planear esta viagem'),
 'pap':('Mas potrèt','Detaye di biahe','Planifiká e biahe'),
}


def attempted_assets(db, scope, product_id):
    excluded=set()
    for row in db.execute("SELECT payload,status FROM isluno_discovery_plans WHERE scope_key=? AND status NOT IN ('queued','consumed_action')",(scope.key,)):
        prior=json.loads(row[0])
        if prior.get('visual_cards'):
            for part in prior.get('parts',[]):
                if part.get('status')!='queued' and part.get('result',{}).get('dispatched') is not False:
                    excluded.update(a['asset_id'] for a in part.get('assets',[]) if a.get('product_id')==product_id)
        elif prior.get('product_ids')==[product_id] and row[1] in {'accepted','claimed','ambiguous','rejected','provider_failed'}:
            excluded.update(prior.get('asset_ids',[]))
    return excluded


def build(store, db, scope, products, decision, button, labels, *, texts=None, common='', question='',
          photo='initial', location='', offset=0, info_offset=0, action_kind=None, offer_selection=True, detail_keys=None):
    """Render product-bound parts. Existing sender owns all durable dispatch states."""
    locale=decision['language'];titles=ACTIONS[locale];parts=[];assets=[];missing=[]
    translations=decision.get('translations') or {}
    def append(body, product_id=None, asset=None, meanings=None, next_question=''):
        wire=messages(body,next_question)
        for index,item in enumerate(wire):
            rich=bool(item.get('attachmentUrl'))
            parts.append({'body':item,'status':'queued','provider_id':None,
                          'product_ids':[product_id] if product_id else [],
                          'assets':[{'product_id':product_id,'asset_id':asset['id']}] if rich and asset else [],
                          'button_meanings':meanings or {} if item.get('buttons') else {},
                          'next_question':next_question if index==len(wire)-1 else ''})
    if question and common.endswith(question):common=common[:-len(question)].rstrip()
    if common.strip():append({'accountId':scope.account_id,'message':common})
    for index,product in enumerate(products):
        product_id=product['id'];source=isluno_understanding.facts(product)
        known=source if locale=='en' else {k:translations.get(product_id,{})[k] for k in source if k in translations.get(product_id,{})}
        # Full detail remains reachable in bounded native pages; no guessed paraphrase.
        detail='\n\n'.join(v for k,v in known.items() if not detail_keys or k in detail_keys) if action_kind=='info' else known.get('summary','')
        from agents.social.isluno_wire import split_text
        chunks=split_text(detail,600) or ['']
        check(0<=info_offset<len(chunks),'invalid_info_page')
        summary=known.get('summary','')
        text=chunks[info_offset] if action_kind=='info' else (texts or {}).get(product_id,summary if units(summary)<=600 else '')
        context=known.get('location_context','')
        if action_kind!='info' and context and context not in text and units(text+'\n\n'+context)<=750:text+='\n\n'+context
        text=product['name']+'\n\n'+text
        gallery=product['gallery'];check(type(offset) is int and 0<=offset<=len(gallery),'invalid_gallery_page')
        excluded=set() if photo=='repeat' else attempted_assets(db,scope,product_id)
        available=[(n,a) for n,a in enumerate(gallery) if n>=offset and a['id'] not in excluded]
        # A details card may reuse its own trip image when that gallery was seen.
        if action_kind=='info' and info_offset==0 and photo!='none' and not available:
            available=list(enumerate(gallery))
        if location:
            # Product captions identify a trip, not the stop depicted in the image.
            available=[(n,a) for n,a in available if a.get('location_id')==location and a.get('location_source_url')]
        page=available[:2 if action_kind=='photos' or photo in {'more','all'} else 1] if photo!='none' else []
        resolved=[]
        for position,asset in page:
            try:url=store.media.url(asset)
            except (MediaUnavailable,OSError,ValueError):missing.append(asset['id'])
            else:resolved.append((position,asset,url));assets.append(asset['id'])
        media_missing=photo!='none' and not resolved
        if media_missing:text+='\n\n'+labels[4]
        cursor=page[-1][0]+1 if page else offset
        choices=[]
        if any(n>=cursor for n,_ in available) and not (location and not available):
            choices.append(button('photos',product_id,titles[0],offset=cursor,photo_location=location))
        choices.append(button('info',product_id,titles[1],info_offset=info_offset+1 if action_kind=='info' and info_offset+1<len(chunks) else 0,photo_location=location))
        if offer_selection:choices.append(button('add',product_id,titles[2]))
        # At most two photos per requested page, with controls only on its final photo.
        for _,asset,url in resolved[:-1]:
            append({'accountId':scope.account_id,'message':product['name'],'attachmentUrl':url,'attachmentType':'image'},product_id,asset)
        final_question=question if index==len(products)-1 else ''
        detached_question=bool(final_question and units(text+'\n\n'+final_question)>1024)
        if final_question and not detached_question:text+='\n\n'+final_question
        body={'accountId':scope.account_id,'message':text,'buttons':choices}
        asset=None
        if resolved:
            _,asset,url=resolved[-1];body.update(attachmentUrl=url,attachmentType='image')
        # Caller binds opaque tokens; exact per-part meanings are assigned after construction.
        append(body,product_id,asset,next_question='' if detached_question else final_question)
        if detached_question:append({'accountId':scope.account_id,'message':final_question},next_question=final_question)
        if media_missing:parts[-1]['media_unavailable']=True
    check(0<len(parts)<=MAX_PARTS,'wire_part_count')
    return parts,assets,missing
