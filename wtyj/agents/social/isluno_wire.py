"""WhatsApp wire limits and lossless presentation parts; no network or intent logic."""
import copy
import re
from shared.isluno_pricing import check

TEXT_LIMIT=4096
INTERACTIVE_LIMIT=1024
MAX_PARTS=6


def text_of(body):
    return body.get('message') or body.get('interactive',{}).get('body',{}).get('text','')


def units(text):
    return len(text.encode("utf-16-le"))//2


def split_text(text,limit):
    """Keep every character, preferring paragraph/line/word boundaries."""
    result=[]
    while units(text)>limit:
        used=0;edge=0
        for char in text:
            width=units(char)
            if used+width>limit:break
            used+=width;edge+=1
        cut=max(text.rfind('\n\n',0,edge),text.rfind('\n',0,edge))
        if cut<edge//3:cut=text.rfind(' ',0,edge)
        if cut<edge//3:cut=edge
        result.append(text[:cut]);text=text[cut:]
    if text:result.append(text)
    return result


def validate_body(body):
    check(isinstance(body,dict) and isinstance(body.get('accountId'),str) and bool(body['accountId']),'wire_account_missing')
    text=text_of(body);check(isinstance(text,str) and bool(text.strip()),'wire_text_missing')
    buttons=body.get('buttons',[])
    check(isinstance(buttons,list) and len(buttons)<=3,'wire_button_count')
    titles=[]
    for button in buttons:
        check(isinstance(button,dict) and button.get('type')=='postback','wire_button_type')
        title=button.get('title');payload=button.get('payload')
        check(isinstance(title,str) and 0<units(title)<=20,'wire_button_title')
        check(isinstance(payload,str) and 0<len(payload)<=256,'wire_button_id')
        titles.append(title)
    check(len(titles)==len(set(titles)),'wire_duplicate_button_title')
    interactive=body.get('interactive')
    if interactive is not None:
        check(isinstance(interactive,dict) and interactive.get('type')=='carousel','wire_interactive_type')
        check(not buttons,'wire_conflicting_controls')
        cards=interactive.get('action',{}).get('cards')
        check(isinstance(cards,list) and 2<=len(cards)<=10,'wire_carousel_cards')
        for index,card in enumerate(cards):
            check(isinstance(card,dict) and units(card.get('body',{}).get('text',''))<=160,'wire_card_text')
            check(card.get('card_index')==index,'wire_card_index')
            header=card.get('header',{})
            check(header.get('type')=='image' and isinstance(header.get('image',{}).get('link'),str)
                  and header['image']['link'].startswith('https://'),'wire_card_image')
            choices=card.get('action',{}).get('buttons',[])
            check(1<=len(choices)<=3,'wire_card_buttons')
            for button in choices:
                reply=button.get('quick_reply',{})
                check(button.get('type')=='quick_reply' and isinstance(reply.get('title'),str) and 0<units(reply['title'])<=20
                      and isinstance(reply.get('id'),str) and 0<len(reply['id'])<=256,'wire_card_button')
    rich=bool(buttons or interactive or body.get('attachmentUrl'))
    check(units(text)<=(INTERACTIVE_LIMIT if rich else TEXT_LIMIT),'wire_text_length')
    if body.get('attachmentUrl'):
        check(body.get('attachmentType') in {'image','video','file'},'wire_attachment_type')
    return body


def messages(body,question='',media_first=False):
    """Keep controls/media on final part and never omit or shorten the answer."""
    original=copy.deepcopy(body)
    if original.get('buttons')==[]:original.pop('buttons')
    text=text_of(original)
    check(isinstance(text,str) and 0<len(text)<=TEXT_LIMIT*MAX_PARTS,'wire_logical_text_length')
    rich=bool(original.get('buttons') or original.get('interactive') or original.get('attachmentUrl'))
    if not rich:
        bodies=[validate_body({'accountId':original['accountId'],'message':part}) for part in split_text(text,TEXT_LIMIT)]
        check(len(bodies)<=MAX_PARTS,'wire_part_count')
        return bodies
    if units(text)<=INTERACTIVE_LIMIT:
        return [validate_body(original)]
    if media_first:
        check(bool(original.get('attachmentUrl')) and not original.get('buttons') and not original.get('interactive'), 'wire_media_first_shape')
        chunks=split_text(text,INTERACTIVE_LIMIT)
        first=copy.deepcopy(original);first['message']=chunks[0]
        bodies=[first]+[{'accountId':original['accountId'],'message':part} for part in split_text(''.join(chunks[1:]),TEXT_LIMIT)]
        check(len(bodies)<=MAX_PARTS,'wire_part_count')
        for item in bodies:validate_body(item)
        check(''.join(text_of(item) for item in bodies)==text,'wire_content_changed')
        return bodies
    # The model's next question belongs with its controls, after the full answer.
    if question and units(question)<=INTERACTIVE_LIMIT and text.endswith(question):
        prefix,suffix=text[:-len(question)],question
    else:
        chunks=split_text(text,INTERACTIVE_LIMIT)
        prefix,suffix=''.join(chunks[:-1]),chunks[-1]
    chunks=split_text(prefix,TEXT_LIMIT)
    if chunks and not chunks[-1].strip():suffix=chunks.pop()+suffix
    bodies=[{'accountId':original['accountId'],'message':part} for part in chunks]
    if 'interactive' in original:original['interactive']['body']['text']=suffix
    else:original['message']=suffix
    bodies.append(original)
    check(len(bodies)<=MAX_PARTS,'wire_part_count')
    for item in bodies:validate_body(item)
    check(''.join(text_of(item) for item in bodies)==text,'wire_content_changed')
    return bodies


def response_metadata(response,data):
    """Keep actionable codes/shape, never provider prose, URLs or credentials."""
    result={'http_status':int(response.status_code)}
    code=data.get('code')
    if isinstance(code,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',code):result['provider_code']=code
    error=data.get('platformError')
    if isinstance(error,dict):
        for key in ('code','subcode'):
            if type(error.get(key)) is int:result['platform_'+key]=error[key]
    warnings=data.get('warnings')
    if isinstance(warnings,list):result['warning_count']=len(warnings)
    details=data.get('data')
    if isinstance(details,dict):result['partial_failure']=bool(details.get('partialFailure'))
    return result
