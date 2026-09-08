"""Public listing extraction is deterministic and treats source as data."""
import importlib.util
from pathlib import Path
import unittest

script = Path(__file__).resolve().parents[2] / "scripts/import_isluno_public_catalog.py"
spec = importlib.util.spec_from_file_location("isluno_public_import", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ImportTests(unittest.TestCase):
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
