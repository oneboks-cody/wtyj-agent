from unittest.mock import Mock
import pytest
from test_mermaid_quote_review import isolated,decision
from test_mermaid_quote_buttons import quote
from agents.marina import marina_agent
from agents.social import mermaid_reservation_workflow as flow,mermaid_reservation_store as store
from shared import state_registry

def test_change_followup_rebuilds_quote(monkeypatch, tmp_path):
 monkeypatch.setenv("MERMAID_DOCUMENT_ROOT", str(tmp_path/"documents"))
 from agents.social import mermaid_documents as docs
 def render(r, target):
  target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b"fixture pdf");return "fixture-hash"
 monkeypatch.setattr(docs,"render_quote_pdf",render)
 r,p=quote(monkeypatch)
 flow.handle_demo_message({'from':'guest','_zernio_account_id':'account','message_id':'change','_zernio_interactive_id':p+'change'},True,use_model=True)
 monkeypatch.setattr(marina_agent,'process_message',Mock(return_value=decision('details',fields={'adults':3},reply='Ik pas het aan.')))
 flow.handle_demo_message({'from':'guest','_zernio_account_id':'account','message_id':'correction','text':'Maak er 3 volwassenen van'},True,use_model=True)
 state=state_registry.wa_get_booking_state('guest')
 updated=store.get_reservation(r['public_id'])
 assert updated['intake']['adults']==3
 assert updated['quote_public_id'] != r['quote_public_id']

 assert updated['state']=='quote_ready'
 assert state['flags'].get('mermaid_quote_change_requested') is None
 assert updated['monetary_snapshot']['total'] > r['monetary_snapshot']['total']
 old=flow.handle_demo_message({'from':'guest','_zernio_account_id':'account','message_id':'old-button','_zernio_interactive_id':p+'accept'},True,use_model=True)
 assert '/pay/' not in old['text']
