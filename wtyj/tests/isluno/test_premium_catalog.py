"""Render all real editorial descriptions and local galleries, with no network."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agents.social.isluno_understanding import facts
from agents.social.isluno_visual import build
from agents.social.isluno_discovery import LABELS
from agents.social.isluno_wire import text_of,validate_body
from shared.isluno_media import MediaLibrary


class PremiumCatalogTests(unittest.TestCase):
    def test_every_product_renders_editorial_copy_and_verified_gallery(self):
        root=Path(__file__).resolve().parents[3]
        config=root/'clients/mermaid/config'
        profile=json.loads((config/'isluno_profile.json').read_text())
        catalog=json.loads((config/'isluno_catalog.json').read_text())
        self.assertEqual(set(profile['product_copy']),{p['id'] for p in catalog['products']})
        self.assertLess((config/'isluno_profile.json').stat().st_size,65536)
        library=MediaLibrary(config/'isluno_catalog.json',root/'wtyj/assets/isluno','https://example.invalid/isluno/media')
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('create table isluno_discovery_plans(payload text,status text,scope_key text)')
        def button(kind,product,title,**kw):return {'type':'postback','title':title,'payload':product+'-'+kind}
        with patch('agents.social.isluno_understanding.active_profile',return_value=profile),patch('agents.social.isluno_visual.active_profile',return_value=profile):
            for product in catalog['products']:
                with self.subTest(product=product['id']):
                    selected=facts(product)
                    self.assertNotEqual(selected['summary'],product['summary'])
                    self.assertNotIn('included not included',' '.join(selected.values()))
                    self.assertNotIn("['",' '.join(selected.values()))
                    parts,assets,missing=build(SimpleNamespace(media=library),db,SimpleNamespace(key='offline',account_id='offline'),[product],{'language':'en'},button,LABELS['en'])
                    self.assertFalse(missing)
                    self.assertEqual(len(assets),min(3,len(product['gallery'])))
                    self.assertIn(selected['summary'],''.join(text_of(p['body']) for p in parts))
                    for part in parts:validate_body(part['body'])
                    if len(assets)>1:self.assertEqual(parts[0]['body']['interactive']['type'],'carousel')
                    else:self.assertIn('attachmentUrl',parts[0]['body'])
