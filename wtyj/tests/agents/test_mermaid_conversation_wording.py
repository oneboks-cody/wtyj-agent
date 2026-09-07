"""Editorial source wiring, checked offline without generating customer replies."""
import json
from pathlib import Path
import pytest
from shared import config_loader
from agents.social import mermaid_understanding

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k: pytest.fail('Network forbidden'))
    root=Path(__file__).resolve().parents[3]/'clients/mermaid/config'
    monkeypatch.setattr(config_loader,'_CONFIG_PATH',str(root/'client.json'))
    monkeypatch.setattr(config_loader,'_cache',{})


def test_actual_prompt_contains_reviewed_vocabulary_for_every_locale():
    persona=config_loader.get_raw()['agent_persona']
    prompt=mermaid_understanding.system_prompt()
    copies=json.loads(Path(config_loader._CONFIG_PATH).with_name('reservation_email.json').read_text())['copies']
    assert set(persona['conversation_wording'])=={'en','nl','de','es','pt','pap'}
    for locale,words in persona['conversation_wording'].items():
        assert words['drinks'] in prompt
        assert words['drinks'].casefold() in copies[locale]['included'].casefold()
        for category in ('equipment', 'facilities'):
            for term in words[category].split('; '):
                assert term.casefold() in copies[locale]['included'].casefold(), (locale, term)
        assert words['style'] in prompt
    assert 'vruchtensappen' in prompt and 'zwemvinnen' in prompt
    assert persona['language_register'] in prompt


def test_overview_is_not_a_translated_brochure_or_booking_push():
    prompt=mermaid_understanding.system_prompt()
    assert '120-180 words' not in prompt
    assert 'OVERVIEW STYLE EXAMPLE' not in prompt
    assert 'The boat journey takes about 1.5 to 2 hours outward' not in prompt
    assert 'An information request is not booking intent' in prompt
    assert 'never combine it with a booking invitation or a second question' in prompt
    assert 'usually 70-110 words in two or three short paragraphs' in prompt
    assert 'Existing conversation history is not a style authority' in prompt
