"""Read-only source/config/media/evidence digests; never reads runtime secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(root,*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
def entry(root,path):return {'path':str(path.relative_to(root)),'sha256':sha(path),'bytes':path.stat().st_size}
def main():
    p=argparse.ArgumentParser();p.add_argument('--dashboard',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    front=Path(args.dashboard).resolve();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    tracked=git(ROOT,'ls-files').splitlines()
    # Explicit source/config allowlist. No client.json, .env, keys, state DB or runtime reads.
    source=[name for name in tracked if (name.startswith(('wtyj/agents/social/','wtyj/shared/','wtyj/dashboard/','wtyj/tests/isluno/','wtyj/scripts/')) and name.endswith('.py'))]
    source += [name for name in tracked if name.startswith('clients/mermaid/config/isluno_') and name.endswith('.json')]
    front_source=[name for name in git(front,'ls-files').splitlines() if (name.startswith('artifacts/unboks/src/') and name.endswith(('.ts','.tsx'))) or name in ('pnpm-lock.yaml','package.json','artifacts/unboks/package.json')]
    catalog_path=ROOT/'clients/mermaid/config/isluno_catalog.json';catalog=json.loads(catalog_path.read_text())
    products=[]
    for product in catalog['products']:
        assets=[]
        for asset in product['gallery']:
            path=ROOT/'wtyj'/asset['delivery_path'];assert sha(path)==asset['sha256']
            assets.append({'id':asset['id'],'path':asset['delivery_path'],'sha256':sha(path)})
        assert product['price_rules'] is None and product['demo_rules']['real_booking_eligible'] is False
        products.append({'id':product['id'],'name':product['name'],'gallery':assets,'sample_version':product['demo_rules']['version'],'pricing_mode':product['readiness']['pricing_mode'],'supplier_facts_unresolved':product['readiness']['source_unresolved']})
    assert len(products)==31
    carried=[]
    for root,folders in [(ROOT,['output/pdf/isluno-07','output/pdf/isluno-08','output/isluno-12']),(front,['output/isluno-09','output/isluno-10','output/isluno-11','output/isluno-12'])]:
        for folder in folders:
            carried.extend({'repository':'backend' if root==ROOT else 'dashboard',**entry(root,path)} for path in sorted((root/folder).rglob('*')) if path.is_file())
    manifest={'schema':'isluno.candidate.v1','fixture_only':True,'external_test_budget_usd':0,
        'backend':{'remote':git(ROOT,'remote','get-url','origin'),'source_commit':git(ROOT,'rev-parse','HEAD'),'branch':git(ROOT,'branch','--show-current'),'frozen_base':'e43d0d174d10b82ee0fd4a394828ea720d3996ad','sources':[entry(ROOT,ROOT/n) for n in source]},
        'dashboard':{'remote':git(front,'remote','get-url','origin'),'source_commit':git(front,'rev-parse','HEAD'),'branch':git(front,'branch','--show-current'),'frozen_base':'2e41f4a209fbafc602419408696a25fa9791dc34','sources':[entry(front,front/n) for n in front_source]},
        'review_target':'release/isluno-demo-v1','binding':'Final evidence-only commit may follow source_commit; verify ancestry and every source digest before release. Exact final PR head is recorded in the review handoff, never inferred as deployed.',
        'defaults':json.loads((ROOT/'clients/mermaid/config/isluno_feature_patch.json').read_text()),'reminders_enabled':json.loads((ROOT/'clients/mermaid/config/isluno_recovery.json').read_text())['enabled'],
        'catalog':{'sha256':sha(catalog_path),'version':catalog.get('version'),'products':products,'gallery_associations':sum(len(x['gallery']) for x in products),'unique_media':len({a['sha256'] for x in products for a in x['gallery']})},
        'carried_artifacts':carried,'current_artifacts':[entry(out,path) for path in sorted(out.rglob('*')) if path.is_file() and path.name!='candidate-manifest.json'],
        'live_only_gates':['outer login','current number ownership / one consumer','live takeover and automation controls','native-language approval','native WhatsApp rendering / native carousels','provider acceptance and delivery/read receipts','media/document deployment configuration','explicit release and bounded live-test authorization']}
    (out/'candidate-manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'products':len(products),'gallery_associations':manifest['catalog']['gallery_associations'],'unique_media':manifest['catalog']['unique_media'],'backend_source_files':len(source),'dashboard_source_files':len(front_source),'carried_artifacts':len(carried)}))
if __name__=='__main__':main()
