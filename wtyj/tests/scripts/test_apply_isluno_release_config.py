"""Synthetic protected config preservation and canonical CAS integration."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

SCRIPT = Path(__file__).parents[2] / 'scripts/apply_isluno_release_config.py'
spec = importlib.util.spec_from_file_location('release_config', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ACCOUNT = hashlib.sha256(b'synthetic-account').hexdigest()


def target():
    return {'slug':'mermaid','business':{'slug':'mermaid'},
            'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic-account']},
            'credentials':{'secret':'synthetic-only'},'features':{'unrelated':True},
            'isluno':{'unrelated':'keep'}, 'owner_controls':{'enabled':True}}


@pytest.mark.parametrize('phase,enabled',[('stage',False),('activate',True),('rollback',False)])
def test_leaf_changes_preserve_binding_credentials_and_unrelated_fields(phase,enabled):
    before=target(); snapshot=copy.deepcopy(before)
    after,changed=m.render(before,phase,1,ACCOUNT)
    assert before==snapshot
    for key in ('slug','business','credentials','channel_account_allowlist','owner_controls'):
        assert after[key]==before[key]
    assert after['features']=={'unrelated':True,'mermaid_cutover_coordination':True,
                              'isluno_itinerary_demo_v1':enabled,'mermaid_reminders':False}
    assert after['isluno']=={'unrelated':'keep','native_carousels':False,
                            'media_base_url':m.MEDIA,'document_base_url':m.DOCUMENTS}
    assert len(changed)==(7 if enabled else 6)
    assert after['mermaid_maintenance']==({'activation_generation':1} if enabled else {})


@pytest.mark.parametrize('generation',[None,True,0,-1,2**63])
def test_invalid_generation_stops(generation):
    with pytest.raises(Exception):m.render(target(),'activate',generation,ACCOUNT)


def test_wrong_account_and_tenant_stop():
    with pytest.raises(Exception):m.render(target(),'stage',account='0'*64)
    doc=target();doc['business']['slug']='other'
    with pytest.raises(Exception):m.render(doc,'stage',account=ACCOUNT)


def test_default_has_no_reads_or_writes(monkeypatch,capsys):
    monkeypatch.setattr(m,'sync',lambda *a,**kw:pytest.fail('write attempted'))
    monkeypatch.setattr(m,'safe_open',lambda *a,**kw:pytest.fail('read attempted'))
    assert m.main([])==0
    assert json.loads(capsys.readouterr().out)=={'status':'offline_default','config_read':False}


def test_cas_failure_precedes_merger_and_backup(tmp_path):
    source=tmp_path/'source.json';source.write_text('{"slug":"mermaid"}')
    dest=tmp_path/'client.json';dest.write_text(json.dumps(target()));dest.chmod(0o600)
    original=dest.read_bytes()
    with pytest.raises(ValueError,match='CAS mismatch'):
        m.sync(source,dest,tmp_path/'backup',apply=True,service_stopped=True,
               expected_sha256='0'*64,merge_function=lambda *a:pytest.fail('merger called'))
    assert dest.read_bytes()==original
    assert not (tmp_path/'backup').exists()


def test_real_exchange_preserves_exact_backup(tmp_path,monkeypatch):
    source=tmp_path/'source.json';source.write_text('{"slug":"mermaid"}')
    dest=tmp_path/'client.json';dest.write_text(json.dumps(target()));dest.chmod(0o600)
    original=dest.read_bytes()
    changed,backup=m.sync(source,dest,tmp_path/'backup',apply=True,service_stopped=True,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        merge_function=lambda source,doc:m.render(doc,'stage',account=ACCOUNT))
    assert backup.read_bytes()==original
    assert json.loads(dest.read_bytes())==m.render(target(),'stage',account=ACCOUNT)[0]
    assert len(changed)==6
