import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from agents.social.isluno_understanding import facts


class EditorialCopyTests(unittest.TestCase):
    def test_copy_is_bound_to_source_without_mutating_catalog(self):
        root=Path(__file__).resolve().parents[3]
        profile=json.loads((root/'clients/mermaid/config/isluno_profile.json').read_text())
        catalog=json.loads((root/'clients/mermaid/config/isluno_catalog.json').read_text())
        product=next(p for p in catalog['products'] if p['id']=='national-park-jeep-safari')
        original=copy.deepcopy(product)
        with patch('agents.social.isluno_understanding.active_profile',return_value=profile):
            result=facts(product)
            self.assertIn('island adventure',result['summary'])
            self.assertIn('pregnant',result['additional_information'])
            self.assertIn('back problems',result['additional_information'])
            self.assertNotIn("['",' '.join(result.values()))
            self.assertEqual(product,original)
            product['source']['content_sha256']='0'*64
            self.assertEqual(facts(product)['summary'],original['summary'])
