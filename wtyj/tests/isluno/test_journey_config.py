"""Offline identity boundaries using synthetic config and the real tenant guard."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shared import config_loader, isluno_config as identity, tenant_guard


class JourneyConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="identity-test-", dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config_path = self.directory / "client.json"
        self.profile_path = self.directory / "isluno_profile.json"
        public_profile = Path(__file__).resolve().parents[3] / "clients/mermaid/config/isluno_profile.json"
        self.profile = json.loads(public_profile.read_text(encoding="utf-8"))
        self.config = {
            "slug": "mermaid", "business": {"slug": "mermaid", "name": "Synthetic Mermaid"},
            "features": {identity.FEATURE: True},
            "channel_account_allowlist": {"mode": "strict", "zernio_accounts": ["synthetic-account"]},
            "provider": "synthetic-provider", "model": "synthetic-model",
            "whatsapp": "synthetic-number", "binding": {"account_id": "synthetic-account"},
            "api_key": "fake-secret-never-used",
        }
        for patcher in (
            patch.dict(os.environ, {"TENANT_ID": "mermaid", "TENANT_SLUG": "",
                                   "TENANT_ACCOUNT_ALLOWLIST_REQUIRED": "true",
                                   "CLIENT_CONFIG_PATH": str(self.config_path)}),
            patch.object(config_loader, "_CONFIG_PATH", str(self.config_path)),
            patch.object(config_loader, "_cache", {}),
            patch.object(config_loader, "_cache_signature", None),
            patch.object(tenant_guard.bm_logger, "LOG_PATH", str(self.directory / "agent.log")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write_config(self.config)
        self.write_profile(self.profile)

    def write_config(self, value):
        # Atomic replacement exercises the real loader's reload detection.
        replacement = self.directory / "replacement.json"
        replacement.write_text(json.dumps(value), encoding="utf-8")
        replacement.replace(self.config_path)

    def write_profile(self, value):
        self.profile_path.write_text(json.dumps(value), encoding="utf-8")

    def assert_unavailable(self):
        self.assertIs(identity.capabilities()["enabled"], False)
        self.assertIsNone(identity.capabilities()["brand"])
        with self.assertRaises(identity.IslunoUnavailable):
            identity.active_profile()

    def scope(self):
        return identity.verified_scope(account_id="synthetic-account",
                                       conversation_id="synthetic-conversation",
                                       customer_ref="synthetic-customer")

    def test_default_off_and_only_literal_true_enables(self):
        for features in ({}, None, [], "enabled", {identity.FEATURE: False},
                         {identity.FEATURE: "true"}, {identity.FEATURE: 1},
                         {identity.FEATURE: None}):
            with self.subTest(features=features):
                config = copy.deepcopy(self.config)
                config["features"] = features
                self.write_config(config)
                self.assert_unavailable()
        config.pop("features")
        self.write_config(config)
        self.assert_unavailable()

    def test_explicit_activation_projects_only_delivered_capabilities(self):
        result = identity.capabilities()
        self.assertIs(result["enabled"], True)
        self.assertIs(result["known"], True)
        self.assertEqual(result["tenant_slug"], "mermaid")
        self.assertEqual(result["journey_type"], "isluno_itinerary_demo_v1")
        self.assertEqual(result["schema_version"], "isluno.v1")
        self.assertEqual(result["mode"], "demo")
        self.assertEqual(result["brand"]["name"], "Isluno")
        self.assertEqual(set(result["document_languages"]), {"en", "nl", "de", "es", "pap", "pt"})
        self.assertEqual(result["capabilities"], {
            "brand_profile": True, "isolated_context": True,
            "itinerary_booking": True, "demo_payment": True, "catalog_editor": False,
        })
        self.assertEqual(identity.require_scope(self.scope()), self.profile)

    def test_environment_and_tenant_identity_are_required(self):
        for environment in (
            {"TENANT_ID": "", "TENANT_SLUG": ""},
            {"TENANT_ID": "foreign", "TENANT_SLUG": "mermaid"},
            {"TENANT_ACCOUNT_ALLOWLIST_REQUIRED": ""},
            {"TENANT_ACCOUNT_ALLOWLIST_REQUIRED": "false"},
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                self.assert_unavailable()
        for slug, business in (("foreign", {"slug": "foreign"}),
                               ("mermaid", {"slug": "foreign"}),
                               ("foreign", {"slug": "mermaid"}),
                               (None, {"slug": "mermaid"}), ("mermaid", {}), ("mermaid", [])):
            with self.subTest(slug=slug, business=business):
                config = copy.deepcopy(self.config)
                config.update(slug=slug, business=business)
                self.write_config(config)
                self.assert_unavailable()

    def test_allowlist_must_be_strict_and_bind_exactly_one_account(self):
        for allowlist in (None, [], {}, {"mode": "permissive", "zernio_accounts": ["synthetic-account"]},
                          *({"mode": "strict", "zernio_accounts": accounts}
                            for accounts in ([], [""], [" "], [1], "synthetic-account",
                                             ["synthetic-account", "foreign-account"]))):
            with self.subTest(allowlist=allowlist):
                config = copy.deepcopy(self.config)
                config["channel_account_allowlist"] = allowlist
                self.write_config(config)
                self.assert_unavailable()
        config.pop("channel_account_allowlist")
        self.write_config(config)
        self.assert_unavailable()

    def test_bad_config_revokes_warm_capability_and_account(self):
        for contents in ("{", "[]", "null", None):
            with self.subTest(contents=contents):
                self.write_config(self.config)
                self.assertIs(identity.capabilities()["enabled"], True)
                self.assertTrue(tenant_guard.is_account_allowed("synthetic-account", "inbound"))
                if contents is None:
                    self.config_path.unlink()
                else:
                    self.config_path.write_text(contents, encoding="utf-8")
                self.assert_unavailable()
                self.assertFalse(tenant_guard.is_account_allowed("synthetic-account", "inbound"))

    def test_missing_or_malformed_profile_fails_closed(self):
        for contents in ("{", "[]", "null", "x" * 65537, None):
            with self.subTest(contents=contents):
                self.write_profile(self.profile)
                self.assertTrue(identity.capabilities()["enabled"])
                if contents is None:
                    self.profile_path.unlink()
                else:
                    self.profile_path.write_text(contents, encoding="utf-8")
                self.assert_unavailable()

    def test_invalid_profile_identity_and_public_copy_fail_closed(self):
        cases = {"tenant_slug": "foreign", "journey_type": "mermaid_legacy",
                 "schema_version": "isluno.v0", "mode": "live", "brand": {},
                 "document_languages": ["en"], "greetings": {"en": "Hello"},
                 "demo_notice": None, "profile_version": ""}
        for field, value in cases.items():
            with self.subTest(field=field):
                profile = copy.deepcopy(self.profile)
                profile[field] = value
                self.write_profile(profile)
                self.assert_unavailable()

    def test_scope_rejects_foreign_and_legacy_identity(self):
        scope = self.scope()
        for field, value in (("tenant_slug", "foreign"), ("account_id", "foreign-account"),
                             ("journey_type", "mermaid_legacy"), ("schema_version", "isluno.v0")):
            with self.subTest(field=field), self.assertRaises(identity.IslunoUnavailable):
                identity.require_scope(replace(scope, **{field: value}))
        with self.assertRaises(identity.IslunoUnavailable):
            identity.require_scope({"tenant_slug": "mermaid"})
        with self.assertRaises(identity.IslunoUnavailable):
            identity.verified_scope(account_id="synthetic-account", conversation_id="conversation",
                                    customer_ref="customer", journey_type="mermaid_legacy")
        for field in ("account_id", "conversation_id", "customer_ref"):
            for value in ("", " ", None, 1, "x" * 513):
                with self.subTest(field=field, value=value), self.assertRaises(identity.IslunoUnavailable):
                    identity.require_scope(replace(scope, **{field: value}))

    def test_scope_keys_separate_every_identity_dimension(self):
        scope = self.scope()
        variants = [replace(scope, **{field: value}) for field, value in (
            ("tenant_slug", "foreign"), ("account_id", "other-account"),
            ("conversation_id", "other-conversation"), ("customer_ref", "other-customer"),
            ("journey_type", "mermaid_legacy"), ("schema_version", "isluno.v0"))]
        self.assertEqual(len({scope.key, *(variant.key for variant in variants)}), 7)
        self.assertEqual(scope.key, self.scope().key)
        left = replace(scope, conversation_id="a:b", customer_ref="c")
        right = replace(scope, conversation_id="a", customer_ref="b:c")
        self.assertNotEqual(left.key, right.key)

    def test_public_projection_excludes_fake_private_fields_and_does_not_mutate_config(self):
        self.profile["api_key"] = "fake-profile-secret"
        self.profile["brand"].update(api_key="fake-brand-secret", account_id="fake-private-account")
        self.write_profile(self.profile)
        original_config = self.config_path.read_bytes()
        original_profile = self.profile_path.read_bytes()
        result = identity.capabilities()
        snapshot = identity.brand_snapshot()
        self.assertEqual(set(snapshot), {"profile_version", "id", "name", "assistant_name", "website", "primary_color"})
        self.assertEqual(result["brand"], snapshot)
        for secret in ("fake-profile-secret", "fake-brand-secret", "fake-private-account",
                       "fake-secret-never-used", "synthetic-provider", "synthetic-model",
                       "synthetic-number", "synthetic-account"):
            self.assertNotIn(secret, json.dumps(result))
        self.scope()
        identity.active_profile()["brand"]["name"] = "Caller mutation"
        self.assertEqual(identity.brand_snapshot()["name"], "Isluno")
        self.assertEqual(self.config_path.read_bytes(), original_config)
        self.assertEqual(self.profile_path.read_bytes(), original_profile)
        self.assertEqual(config_loader.get_raw(), self.config)
