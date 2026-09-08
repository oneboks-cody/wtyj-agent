"""Catalog behavior tests with synthetic prices, no live config or requests."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from shared.isluno_catalog import CatalogError, CatalogConflict, CatalogStore, quote_rules, validate_catalog


def synthetic_catalog():
    return {
        "schema_version": "isluno.v1", "tenant_slug": "mermaid", "brand": "isluno", "version": "fixture-v1",
        "products": [{
            "id": "fixture-cruise", "name": "Synthetic Cruise", "category": "Fixture", "summary": "Synthetic test product.", "enabled": True,
            "source": {"url": "https://example.invalid/tours/fixture", "observed_at": "2026-09-08T00:00:00+00:00", "fixture_only": True},
            "supplier_ref": None, "gallery": [], "inclusions": ["Synthetic lunch"],
            "guest_rules": {"minimum_age": 0, "maximum_age": 120, "adult_min_age": 13},
            "price_rules": {"currency": "USD", "currency_exponent": 2, "basis": "per_person", "taxes_fees": "included", "payment_terms": "demo_full_payment", "evidence": "Synthetic fixture, not a supplier price.",
                            "age_bands": [{"id": "infant", "minimum_age": 0, "maximum_age": 5, "amount_minor": 0},
                                          {"id": "child", "minimum_age": 6, "maximum_age": 12, "amount_minor": 5000},
                                          {"id": "adult", "minimum_age": 13, "maximum_age": 120, "amount_minor": 10000}]},
            "schedule": {"timezone": "America/Curacao", "weekdays": [0, 1, 2, 3, 4, 5, 6],
                         "slots": [{"id": "morning", "start": "09:00", "duration_minutes": 180}], "evidence": "Synthetic schedule."},
            "options": [], "pickup": {"mode": "meeting_point", "meeting_point": "Synthetic pier"},
            "policies": {"verified": True, "cancellation": "Demo only; no real reservation.", "safety": "Synthetic eligibility.", "evidence": "Synthetic fixture."},
        }],
    }


class CatalogTests(unittest.TestCase):
    def test_sample_rules_preserve_unknown_facts_and_are_demo_only(self):
        catalog = synthetic_catalog()
        product = catalog["products"][0]
        keys = ("guest_rules", "price_rules", "schedule", "options", "pickup", "policies")
        sample = {key: copy.deepcopy(product[key]) for key in keys}
        for key in keys:
            product[key] = None
        product["demo_rules"] = {"authority": "user_approved_demo_sample", "version": "fixture-sample-v1",
                                 "real_booking_eligible": False, "label": "DEMO SAMPLE — synthetic rules",
                                 "approval_ref": "Synthetic fixture approval", "rules": sample}
        validated = validate_catalog(catalog)
        product = validated["products"][0]
        self.assertIsNone(product["price_rules"])
        self.assertTrue(product["readiness"]["quotable"])
        self.assertIn("exact_prices_unverified", product["readiness"]["source_unresolved"])
        selected = quote_rules(product, mode="demo")
        self.assertEqual("demo_sample", selected["pricing_mode"])
        self.assertFalse(selected["real_booking_eligible"])
        with self.assertRaises(CatalogError):
            quote_rules(product, mode="real")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "isluno_catalog.json"
            path.write_text(json.dumps(validated))
            store = CatalogStore(path)
            before = store.snapshot()
            change = copy.deepcopy(product["demo_rules"])
            change["rules"]["price_rules"]["age_bands"][-1]["amount_minor"] = 20000
            after = store.publish([{"id": product["id"], "changes": {"demo_rules": change}}], before["revision"])
            updated = after["catalog"]["products"][0]
            self.assertNotEqual(selected["rules_version"], updated["demo_rules"]["version"])
            self.assertEqual(10000, selected["rules"]["price_rules"]["age_bands"][-1]["amount_minor"])
            self.assertIsNone(updated["price_rules"])

    def test_readiness_requires_explicit_facts_not_advertised_from_or_client_flag(self):
        catalog = synthetic_catalog()
        p = catalog["products"][0]
        p["price_rules"] = None
        p["readiness"] = {"quotable": True}
        p["source_claims"] = {"advertised_from": "$1"}
        result = validate_catalog(catalog)
        self.assertFalse(result["products"][0]["readiness"]["quotable"])
        self.assertIn("exact_prices_unverified", result["products"][0]["readiness"]["unresolved"])
        self.assertIsNone(result["products"][0]["price_rules"])

    def test_complete_fixture_quotable_and_defensive_copy(self):
        source = synthetic_catalog()
        result = validate_catalog(source)
        self.assertTrue(result["products"][0]["readiness"]["quotable"])
        result["products"][0]["price_rules"]["age_bands"][1]["amount_minor"] = 9
        self.assertEqual(5000, source["products"][0]["price_rules"]["age_bands"][1]["amount_minor"])

    def test_age_prices_reject_overlap_and_report_gaps(self):
        catalog = synthetic_catalog()
        catalog["products"][0]["price_rules"]["age_bands"][1]["minimum_age"] = 5
        with self.assertRaises(CatalogError):
            validate_catalog(catalog)
        catalog["products"][0]["price_rules"]["age_bands"][1]["minimum_age"] = 7
        self.assertIn("age_price_gap", validate_catalog(catalog)["products"][0]["readiness"]["unresolved"])

    def test_invalid_price_and_schedule_values_fail(self):
        for value in [-1, 1.1, True, "100"]:
            catalog = synthetic_catalog()
            catalog["products"][0]["price_rules"]["age_bands"][1]["amount_minor"] = value
            with self.subTest(value=value), self.assertRaises(CatalogError):
                validate_catalog(catalog)
        for field, value in [("weekdays", [True]), ("weekdays", [{}]), ("timezone", "not/a-zone"), ("slots", [])]:
            catalog = synthetic_catalog()
            catalog["products"][0]["schedule"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(CatalogError):
                validate_catalog(catalog)

    def test_wrong_identity_and_duplicate_product_rejected(self):
        for key, value in [("tenant_slug", "another-tenant"), ("brand", "mermaid"), ("schema_version", "v0")]:
            catalog = synthetic_catalog()
            catalog[key] = value
            with self.subTest(key=key), self.assertRaises(CatalogError):
                validate_catalog(catalog)
        catalog = synthetic_catalog()
        catalog["products"] *= 2
        with self.assertRaises(CatalogError):
            validate_catalog(catalog)

    def test_publish_conflict_history_and_snapshot_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "isluno_catalog.json"
            path.write_text(json.dumps(synthetic_catalog()))
            store = CatalogStore(path)
            before = store.snapshot()
            historical_quote_snapshot = copy.deepcopy(before["catalog"]["products"][0])
            prices = copy.deepcopy(historical_quote_snapshot["price_rules"])
            prices["age_bands"][-1]["amount_minor"] = 15000
            after = store.publish([{"id": "fixture-cruise", "changes": {"price_rules": prices}}], before["revision"])
            self.assertNotEqual(before["revision"], after["revision"])
            self.assertEqual(10000, historical_quote_snapshot["price_rules"]["age_bands"][-1]["amount_minor"])
            archived = json.loads((path.with_name("isluno_catalog_versions") / (before["revision"] + ".json")).read_text())
            self.assertEqual(before["catalog"], archived)
            with self.assertRaises(CatalogConflict):
                store.publish([{"id": "fixture-cruise", "changes": {"name": "Stale change"}}], before["revision"])
            self.assertEqual(after, store.snapshot())

    def test_concurrent_publish_has_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "isluno_catalog.json"
            path.write_text(json.dumps(synthetic_catalog()))
            store = CatalogStore(path)
            before = store.snapshot()
            def publish(name):
                try:
                    store.publish([{"id": "fixture-cruise", "changes": {"name": name}}], before["revision"])
                    return "published"
                except CatalogConflict:
                    return "conflict"
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(publish, ["First edit", "Second edit"]))
            self.assertEqual(["conflict", "published"], sorted(results))
            self.assertEqual(1, len(list(path.with_name("isluno_catalog_versions").glob("*.json"))))

    def test_editor_cannot_change_identity_readiness_or_inject_media(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "isluno_catalog.json"
            path.write_text(json.dumps(synthetic_catalog()))
            store = CatalogStore(path)
            before = store.snapshot()
            for changes in [{"id": "different"}, {"source": {}}, {"readiness": {"quotable": True}},
                            {"gallery": [{"id": "injected", "source_url": "https://127.0.0.1/admin"}]}]:
                with self.subTest(changes=changes), self.assertRaises(CatalogError):
                    store.publish([{"id": "fixture-cruise", "changes": changes}], before["revision"])
            self.assertEqual(before, store.snapshot())

    def test_gallery_order_and_captions_can_change_without_mutating_old_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = synthetic_catalog()
            for index, digit in enumerate(["a", "b"]):
                catalog["products"][0]["gallery"].append({
                    "id": "image-" + digit, "order": index, "caption": "Old caption", "validation_status": "verified",
                    "source_url": "https://example.invalid/" + digit + ".png", "sha256": digit * 64,
                    "format": "png", "bytes": 100, "width": 20, "height": 20,
                    "delivery_path": "assets/isluno/" + digit * 64 + ".png",
                })
            path = Path(directory) / "isluno_catalog.json"
            path.write_text(json.dumps(catalog))
            store = CatalogStore(path)
            before = store.snapshot()
            gallery = list(reversed(copy.deepcopy(before["catalog"]["products"][0]["gallery"])))
            for index, asset in enumerate(gallery):
                asset["order"] = index
                asset["caption"] = "New caption"
            after = store.publish([{"id": "fixture-cruise", "changes": {"gallery": gallery}}], before["revision"])
            self.assertEqual("image-b", after["catalog"]["products"][0]["gallery"][0]["id"])
            self.assertEqual("image-a", before["catalog"]["products"][0]["gallery"][0]["id"])
            self.assertEqual("Old caption", before["catalog"]["products"][0]["gallery"][0]["caption"])

    def test_repository_catalog_and_all_assets_match_import_manifest(self):
        root = Path(__file__).resolve().parents[3]
        path = root / "clients/mermaid/config/isluno_catalog.json"
        catalog = validate_catalog(json.loads(path.read_text()))
        report = json.loads(path.with_name("isluno_inventory_report.json").read_text())
        self.assertEqual(report["discovered_product_count"], len(catalog["products"]))
        self.assertEqual(set(p["id"] for p in report["products"]), set(p["id"] for p in catalog["products"]))
        self.assertFalse(report["missing_assets"])
        for product in catalog["products"]:
            for asset in product["gallery"]:
                data = (root / "wtyj" / asset["delivery_path"]).read_bytes()
                self.assertEqual(asset["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(asset["bytes"], len(data))
                self.assertNotIn("?", asset["source_url"])
        # Source 'from' claims never constitute exact supplier prices. Calvin's
        # explicit demo clarification allows separate, labelled sample rules.
        self.assertTrue(all(p["price_rules"] is None and p["readiness"]["quotable"] for p in catalog["products"]))
        self.assertTrue(all(p["readiness"]["pricing_mode"] == "demo_sample" and p["readiness"]["source_unresolved"] for p in catalog["products"]))
        self.assertTrue(all(quote_rules(p, mode="demo")["real_booking_eligible"] is False for p in catalog["products"]))


if __name__ == "__main__":
    unittest.main()
