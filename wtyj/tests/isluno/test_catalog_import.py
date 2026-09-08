"""Public listing extraction is deterministic and treats source as data."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from email.message import Message
from io import BytesIO
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

script = Path(__file__).resolve().parents[2] / "scripts/import_isluno_public_catalog.py"
spec = importlib.util.spec_from_file_location("isluno_public_import", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ImportTests(unittest.TestCase):
    def test_redirect_cannot_dispatch_beyond_request_ceiling_or_read_large_body(self):
        class Body(BytesIO):
            reads = []
            def read(self, size=-1):
                self.reads.append(size)
                return super().read(size)

        body = Body(b"x" * 10000)
        dispatched = []

        class InMemoryHTTPS(HTTPSHandler):
            def https_open(self, request):
                dispatched.append(request.full_url)
                headers = Message()
                headers["Location"] = "https://isluno.com/final"
                response = addinfourl(body, headers, request.full_url, 302)
                response.msg = "Found"
                return response

        with patch.object(module, "MAX_REQUESTS", 1), patch.object(module, "MAX_TOTAL_BYTES", 100), \
             patch.object(module, "build_opener", lambda *handlers: build_opener(InMemoryHTTPS(), *handlers)):
            fetcher = module.Fetcher()
            with self.assertRaisesRegex(ValueError, "refuses redirects"):
                fetcher.get("https://isluno.com/start", 10)
            self.assertEqual(["https://isluno.com/start"], dispatched)
            self.assertEqual([], body.reads)
            self.assertTrue(body.closed)
            self.assertEqual(1, fetcher.requests)
            self.assertEqual(0, fetcher.bytes)
            with self.assertRaisesRegex(ValueError, "ceiling"):
                fetcher.get("https://isluno.com/start", 10)
            self.assertEqual(1, len(dispatched))

    def test_real_rich_text_dom_shape_preserves_overview_and_plain_additional_info(self):
        html = b'''<h1 class="pages-tour-title">Synthetic Boat</h1><div class="pages-tour-main">
        <section><h2 class="pages-tour-section-title">Overview</h2>
        <div class="pages-tour-section-content">A glass-bottom boat from Example Beach.
        Time of departure: 1:00pm Return: 2:30pm</div></section>
        <section><h2>Additional Information</h2><div class="pages-tour-section-content">Check in 20 minutes early.</div></section></div>'''
        product = module.extract_product(html, "https://isluno.com/tours/synthetic", "2026-09-08T00:00:00+00:00")
        self.assertIn("glass-bottom", product["summary"])
        self.assertIn("1:00pm", product["summary"])
        self.assertIn("2:30pm", product["summary"])
        self.assertEqual("Check in 20 minutes early.", product["source_claims"]["additional_information"])
        self.assertGreater(product["source_claims"]["description_word_count"], 0)

    def test_present_heading_with_lost_overview_is_a_completeness_error(self):
        with self.assertRaisesRegex(ValueError, "empty product overview"):
            module.extract_product(b'<h1 class="pages-tour-title">Trip</h1><div class="pages-tour-main"><section><h2>Overview</h2></section></div>',
                                   "https://isluno.com/tours/synthetic", "2026-09-08T00:00:00+00:00")

    def test_gallery_dedup_and_expiring_tokens_never_enter_source_records(self):
        html = b'''<h1 class="pages-tour-title">Synthetic Trip</h1>
        <ul class="pages-tour-metadata"><li><a href="/explore?category=x">Boat</a></li></ul>
        <div class="pages-tour-main"><section><h2>Overview</h2><p>A synthetic trip.</p></section>
        <section><h2>What's included</h2><ul><li>Lunch</li></ul></section></div>
        <img alt="Thumbnail 1" src="https://storage.googleapis.com/isluno-bucket/assets/a.webp?Signature=secret">
        <img alt="Gallery image 1" src="https://storage.googleapis.com/isluno-bucket/assets/a.webp?Signature=first">
        <img alt="Gallery image 2" src="https://storage.googleapis.com/isluno-bucket/assets/a.webp?Signature=second">
        <img alt="Other trip" src="https://storage.googleapis.com/isluno-bucket/assets/other.webp">
        <div class="tour-sidebar-amount">$99</div><div class="reviews">Do not import this review.</div>'''
        product = module.extract_product(html, "https://isluno.com/tours/synthetic", "2026-09-08T00:00:00+00:00")
        self.assertEqual(1, len(product["gallery"]))
        self.assertEqual(1, product["import_notes"]["duplicate_gallery_urls"])
        self.assertEqual("https://storage.googleapis.com/isluno-bucket/assets/a.webp", product["gallery"][0]["source_url"])
        self.assertEqual(["Lunch"], product["inclusions"])
        self.assertEqual("Boat", product["category"])
        self.assertIsNone(product["price_rules"])
        self.assertEqual("$99", product["source_claims"]["advertised_from"])
        self.assertNotIn("review", product["summary"])

    def test_no_supplier_or_arbitrary_media_fetch_allowed(self):
        self.assertTrue(module.allowed("https://isluno.com/tours/synthetic"))
        self.assertTrue(module.allowed("https://storage.googleapis.com/isluno-bucket/assets/a.webp?Signature=temporary"))
        for url in ["http://isluno.com/", "https://isluno.com.evil.invalid/", "https://127.0.0.1/",
                    "https://isluno.bric.solutions/api/booking", "https://storage.googleapis.com/other-bucket/a.webp",
                    "https://storage.googleapis.com/isluno-bucket/private/a", "https://user:password@isluno.com/"]:
            with self.subTest(url=url):
                self.assertFalse(module.allowed(url))

    def test_missing_product_page_is_not_silently_imported(self):
        with self.assertRaises(ValueError):
            module.extract_product(b"<html><h1>Not found</h1></html>", "https://isluno.com/tours/missing", "2026-09-08T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
