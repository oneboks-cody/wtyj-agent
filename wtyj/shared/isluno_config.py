"""Read-only Isluno identity/capability boundary within the Mermaid tenant."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from shared import config_loader, tenant_guard

SCHEMA_VERSION = "isluno.v1"
TENANT = "mermaid"
JOURNEY = "isluno_itinerary_demo_v1"
FEATURE = JOURNEY
LANGUAGES = frozenset({"en", "nl", "de", "es", "pap", "pt"})


class IslunoUnavailable(PermissionError):
    pass


def _identity_valid(raw):
    business = raw.get("business") or {}
    return isinstance(business, dict) and raw.get("slug") == TENANT and business.get("slug") == TENANT


def _strict_runtime_valid(raw):
    expected = (os.environ.get("TENANT_ID") or os.environ.get("TENANT_SLUG") or "").strip().lower()
    required = os.environ.get("TENANT_ACCOUNT_ALLOWLIST_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"}
    allowlist = raw.get("channel_account_allowlist")
    if not _identity_valid(raw) or expected != TENANT or not required or not isinstance(allowlist, dict):
        return False
    accounts = allowlist.get("zernio_accounts")
    return allowlist.get("mode") == "strict" and isinstance(accounts, list) and len(accounts) == 1 and isinstance(accounts[0], str) and bool(accounts[0].strip())


def profile_path():
    return Path(config_loader._CONFIG_PATH).with_name("isluno_profile.json")


def active_profile():
    """Return only an explicitly enabled, strict-tenant profile; never write config."""
    raw = config_loader.get_raw() or {}
    features = raw.get("features") or {}
    if not isinstance(features, dict) or features.get(FEATURE) is not True or not _strict_runtime_valid(raw):
        raise IslunoUnavailable("Isluno capability is disabled or unavailable")
    try:
        path = profile_path()
        if path.stat().st_size > 65536:
            raise ValueError("profile size")
        profile = json.loads(path.read_text(encoding="utf-8"))
        brand = profile.get("brand")
        if (profile.get("schema_version") != SCHEMA_VERSION or profile.get("tenant_slug") != TENANT
                or profile.get("journey_type") != JOURNEY or profile.get("mode") != "demo"
                or not isinstance(brand, dict) or brand.get("id") != "isluno" or brand.get("name") != "Isluno"):
            raise ValueError("profile identity")
        if set(profile.get("document_languages", [])) != LANGUAGES:
            raise ValueError("profile languages")
        for group in ("greetings", "demo_notice"):
            values = profile.get(group)
            if not isinstance(values, dict) or set(values) != LANGUAGES or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values.values()):
                raise ValueError("profile copy")
        for key in ("assistant_name", "website", "primary_color"):
            if not isinstance(brand.get(key), str) or not brand[key].strip() or len(brand[key]) > 300:
                raise ValueError("profile public identity")
        welcome = profile.get("welcome_media")
        if welcome is not None and (not isinstance(welcome, dict) or set(welcome) != {"sha256", "format", "bytes", "width", "height"}
                or not isinstance(welcome.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", welcome["sha256"])
                or welcome.get("format") != "png" or type(welcome.get("bytes")) is not int or not 1 <= welcome["bytes"] <= 5 * 1024 * 1024
                or type(welcome.get("width")) is not int or type(welcome.get("height")) is not int
                or not 1 <= welcome["width"] <= 4096 or not 1 <= welcome["height"] <= 4096):
            raise ValueError("profile welcome media")
        copies = profile.get('product_copy', {})
        if profile.get('gallery_mode','single') not in {'single','carousel'}:
            raise ValueError('profile gallery mode')
        if not isinstance(copies, dict) or len(copies) > 50:
            raise ValueError('profile product copy')
        for product_id, editorial in copies.items():
            if (not isinstance(product_id, str) or not re.fullmatch(r'[a-z0-9-]{1,100}', product_id)
                    or not isinstance(editorial, dict) or set(editorial) != {'source_sha256', 'facts'}
                    or not isinstance(editorial['source_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', editorial['source_sha256'])
                    or not isinstance(editorial['facts'], dict) or len(editorial['facts']) > 30
                    or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() or len(v)>2000 for k,v in editorial['facts'].items())):
                raise ValueError('profile product copy')
        if not isinstance(profile.get("profile_version"), str) or not profile["profile_version"].strip():
            raise ValueError("profile version")
        return copy.deepcopy(profile)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise IslunoUnavailable("Isluno profile is unavailable") from exc


def brand_snapshot(profile=None):
    profile = profile if profile is not None else active_profile()
    # Explicit projection: unknown fields and any accidentally added private
    # config values cannot enter the API, prompt or new session identity.
    return {"profile_version": profile["profile_version"],
            **{key: profile["brand"][key] for key in ("id", "name", "assistant_name", "website", "primary_color")}}


def capabilities():
    raw = config_loader.get_raw() or {}
    known = _identity_valid(raw)
    response = {"schema_version": SCHEMA_VERSION, "tenant_slug": TENANT if known else None,
                "known": known, "enabled": False, "journey_type": JOURNEY if known else None,
                "brand": None, "mode": "demo" if known else None,
                "capabilities": {"brand_profile": False, "isolated_context": False,
                                 "itinerary_booking": False, "demo_payment": False, "catalog_editor": False, "itinerary_workspace": False}}
    try:
        profile = active_profile()
    except IslunoUnavailable:
        return response
    response.update({"enabled": True, "brand": brand_snapshot(profile), "document_languages": profile["document_languages"]})
    response["capabilities"].update({"brand_profile": True, "isolated_context": True, "itinerary_booking": True, "demo_payment": True, "catalog_editor": True, "itinerary_workspace": True})
    return response


@dataclass(frozen=True)
class JourneyScope:
    tenant_slug: str
    account_id: str
    conversation_id: str
    customer_ref: str
    journey_type: str = JOURNEY
    schema_version: str = SCHEMA_VERSION

    @property
    def key(self):
        values = [self.tenant_slug, self.account_id, self.conversation_id, self.customer_ref, self.journey_type, self.schema_version]
        return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def require_scope(scope):
    if not isinstance(scope, JourneyScope) or scope.tenant_slug != TENANT or scope.journey_type != JOURNEY or scope.schema_version != SCHEMA_VERSION:
        raise IslunoUnavailable("Invalid Isluno tenant or journey scope")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 512 for value in (scope.account_id, scope.conversation_id, scope.customer_ref)):
        raise IslunoUnavailable("Missing verified channel identity")
    profile = active_profile()
    if not tenant_guard.is_account_allowed(scope.account_id, "inbound"):
        raise IslunoUnavailable("Channel account is not allowed for Isluno")
    return profile


def verified_scope(*, account_id, conversation_id, customer_ref, journey_type=JOURNEY):
    scope = JourneyScope(TENANT, account_id, conversation_id, customer_ref, journey_type)
    require_scope(scope)
    return scope
