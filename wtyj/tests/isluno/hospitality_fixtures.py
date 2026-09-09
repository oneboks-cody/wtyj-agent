"""Explicit scripted model decisions; these are NOT live model quality evidence."""
def hospitality(text, question='', *, action='none', evidence='', memory=None, stage='exploration', photo='none', replies=None):
    if action in {'add','new','update'} and 'pending' not in (replies or {}):
        question = question or 'Could you share the {missing_field}?'
    return {'stage': stage, 'memory': memory or {}, 'discussed': [],
            'consent': {'action': action, 'evidence': evidence}, 'photo': photo,
            'replies': {'browsing': {'paragraphs': [text], 'question': question}, **(replies or {})}}


def operation_replies():
    return {
        'pending': {'paragraphs': ["Let's put the details together for your demo itinerary."], 'question': 'Could you share the {missing_field}?'},
        'saved': {'paragraphs': ["Your choices are together in a demo draft:\n{items}", 'The draft total is {total}.'], 'question': 'Would you like to review the full itinerary?'},
        'error': {'paragraphs': ["I couldn't complete that change.", '{operation}'], 'question': 'Would another day suit you?'},
        'review': {'paragraphs': ["Of course, I understand you'd prefer help from the team.", '{operation}'], 'question': ''},
        'choose_item': {'paragraphs': ['{operation}'], 'question': ''},
        'choose_product': {'paragraphs': ['{operation}'], 'question': ''},
        'cancelled': {'paragraphs': ['{operation}'], 'question': ''},
        'no_active': {'paragraphs': ['{operation}'], 'question': ''},
        'approval_unavailable': {'paragraphs': ['{operation}'], 'question': ''},
    }
