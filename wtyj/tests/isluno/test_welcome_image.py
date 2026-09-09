"""First-contact Isluno brand image stays scoped, first and one-time."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_journey_config as fixtures
from test_catalog import synthetic_catalog
from agents.social.isluno_discovery import DiscoveryStore
from agents.social.isluno_wire import messages, text_of
from shared.isluno_media import MediaLibrary, MediaUnavailable

NOW = datetime(2026, 9, 9, 20, tzinfo=timezone.utc)


class BrandMedia:
    digest = 'a' * 64
    def welcome_url(self):
        return 'https://example.invalid/isluno/' + self.digest + '.jpg', self.digest
    def url(self, asset):
        return 'https://example.invalid/trip/' + asset['id'] + '.jpg'


class WelcomeImageTests(unittest.TestCase):
    write_config = fixtures.JourneyConfigTests.write_config
    write_profile = fixtures.JourneyConfigTests.write_profile
    scope = fixtures.JourneyConfigTests.scope

    def setUp(self):
        fixtures.JourneyConfigTests.setUp(self)
        self.catalog_path = self.directory / 'isluno_catalog.json'
        self.catalog_path.write_text(json.dumps(synthetic_catalog()))
        self.store = DiscoveryStore(self.directory / 'state.db', self.catalog_path,
                                    media=BrandMedia(), clock=lambda: NOW)

    def decision(self, products=None):
        return {'language':'en', 'product_ids':products or [], 'fact_keys':[],
                'intent':'discover', 'question':''}

    def test_first_welcome_has_brand_image_above_text(self):
        plan = self.store.plan(self.scope(), 'welcome-1', NOW.isoformat(), self.decision(),
                               response_text='Welcome to Curaçao!\n\nWhat would you love to experience?',
                               hospitality={'photo':'none'}, welcome_image=True)
        self.assertEqual(len(plan['parts']), 1)
        body = plan['parts'][0]['body']
        self.assertEqual(body['attachmentType'], 'image')
        self.assertIn(BrandMedia.digest, body['attachmentUrl'])
        self.assertEqual(plan['brand_asset_id'], BrandMedia.digest)

    def test_long_welcome_keeps_media_on_first_part_and_all_text(self):
        text = ('Welcome to Curaçao. ' * 90) + '\n\nWhat sounds good to you?'
        bodies = messages({'accountId':'account', 'message':text,
                           'attachmentUrl':'https://example.invalid/logo.jpg',
                           'attachmentType':'image'}, media_first=True)
        self.assertIn('attachmentUrl', bodies[0])
        self.assertTrue(all('attachmentUrl' not in body for body in bodies[1:]))
        self.assertEqual(''.join(text_of(body) for body in bodies), text)

    def test_followup_does_not_attach_brand_image(self):
        plan = self.store.plan(self.scope(), 'followup-1', NOW.isoformat(), self.decision(),
                               response_text='What kind of day sounds good?',
                               hospitality={'photo':'none'}, welcome_image=False)
        self.assertNotIn('attachmentUrl', plan['parts'][0]['body'])
        self.assertIsNone(plan['brand_asset_id'])

    def test_product_recommendation_never_uses_brand_as_trip_media(self):
        decision = self.decision(['fixture-cruise'])
        decision['fact_keys'] = ['summary']
        plan = self.store.plan(self.scope(), 'product-1', NOW.isoformat(), decision,
                               response_text='This trip fits a relaxed day.',
                               hospitality={'photo':'initial'}, welcome_image=True,
                               card_texts={'fixture-cruise':'A relaxed day on the water.'})
        self.assertIsNone(plan['brand_asset_id'])
        self.assertTrue(all(BrandMedia.digest not in part['body'].get('attachmentUrl','') for part in plan['parts']))

    def test_supplied_asset_is_exactly_allowlisted_and_converts_to_jpeg(self):
        root = Path(__file__).resolve().parents[2]
        asset = root / 'assets/isluno/brand/cdc5d181bc940131b48d1e4b7cd64e0a46ccf00d2295e377bebc7a96420f5ded.png'
        profile = {'welcome_media': {'sha256':'cdc5d181bc940131b48d1e4b7cd64e0a46ccf00d2295e377bebc7a96420f5ded',
                   'format':'png','bytes':109599,'width':1200,'height':630}}
        library = MediaLibrary(self.catalog_path, root / 'assets/isluno', 'https://example.invalid/isluno/media')
        with patch('shared.isluno_media.active_profile', return_value=profile):
            url,digest = library.welcome_url()
            data = library.jpeg(digest)
            self.assertTrue(asset.is_file())
            self.assertTrue(data.startswith(b'\xff\xd8'))
            self.assertEqual(url, 'https://example.invalid/isluno/media/' + digest + '.jpg')
            with self.assertRaises(MediaUnavailable):
                library.jpeg('b' * 64)


if __name__ == '__main__':
    unittest.main()
