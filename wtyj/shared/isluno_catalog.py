"""Versioned tenant-owned Isluno catalog; no supplier/network side effects.

The caller supplies an authorized catalog path. Runtime capability/auth checks
belong to the channel/API boundary, not to catalog text or editor body fields.
"""
from __future__ import annotations

import copy
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = "isluno.v1"
TENANT = "mermaid"
MAX_PRODUCTS = 500
MAX_IMAGES = 100
CURRENCIES = {"USD": 2, "EUR": 2, "XCG": 2}
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}\Z")
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
_EDITABLE = {
    "name", "category", "summary", "gallery", "schedule", "guest_rules",
    "price_rules", "options", "pickup", "inclusions", "policies", "enabled",
    "demo_rules",
}
_RULE_KEYS = {"guest_rules", "price_rules", "schedule", "options", "pickup", "policies"}


class CatalogError(ValueError):
    pass


class CatalogConflict(CatalogError):
    pass


def _check(condition, message):
    if not condition:
        raise CatalogError(message)


def _text(value, label, maximum=1000, empty=False):
    _check(isinstance(value, str) and len(value) <= maximum and (empty or bool(value.strip())), label)


def _integer(value, label, minimum=0, maximum=100000000):
    _check(type(value) is int and minimum <= value <= maximum, label)


def _id(value, label):
    _check(isinstance(value, str) and _ID.fullmatch(value), label)


def _public_url(value, label):
    _text(value, label, 2000)
    url = urlsplit(value)
    _check(url.scheme == "https" and url.hostname and not url.username and not url.password
           and not url.query and not url.fragment, label + " must be canonical HTTPS without query tokens")


def revision(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _validate_gallery(gallery):
    _check(isinstance(gallery, list) and len(gallery) <= MAX_IMAGES, "gallery size")
    seen = set()
    for index, asset in enumerate(gallery):
        _check(isinstance(asset, dict), "gallery entry")
        _id(asset.get("id"), "asset id")
        _check(asset["id"] not in seen, "duplicate gallery asset")
        seen.add(asset["id"])
        _check(type(asset.get("order")) is int and asset["order"] == index, "gallery order must be consecutive")
        _public_url(asset.get("source_url"), "asset source")
        _text(asset.get("caption", ""), "asset caption", 500, empty=True)
        _check(asset.get("validation_status") in {"verified", "missing", "rejected"}, "asset validation status")
        if asset["validation_status"] == "verified":
            digest = asset.get("sha256")
            _check(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest), "asset digest")
            _check(asset.get("delivery_path") == "assets/isluno/" + digest + "." + asset.get("format", ""), "asset path must be content-addressed")
            _check(asset.get("format") in {"webp", "jpeg", "png"}, "asset format")
            _integer(asset.get("bytes"), "asset bytes", 1, 5 * 1024 * 1024)
            for axis in ("width", "height"):
                _integer(asset.get(axis), "asset " + axis, 1, 12000)


def _validate_rules(product):
    missing = []
    guest = product.get("guest_rules")
    price = product.get("price_rules")
    schedule = product.get("schedule")
    if guest is None:
        missing.append("guest_rules_unverified")
    else:
        _check(isinstance(guest, dict), "guest rules")
        _check(not set(guest) - {"minimum_age", "maximum_age", "adult_min_age", "max_guests", "children_require_adult"}, "unsupported guest rule")
        _integer(guest.get("minimum_age"), "minimum age", 0, 120)
        _integer(guest.get("maximum_age"), "maximum age", guest["minimum_age"], 120)
        _integer(guest.get("adult_min_age"), "adult age", guest["minimum_age"], guest["maximum_age"])
        if "max_guests" in guest:
            _integer(guest["max_guests"], "maximum guests", 1, 1000)
        if "children_require_adult" in guest:
            _check(type(guest["children_require_adult"]) is bool, "children require adult must be boolean")
    if price is None:
        missing.append("exact_prices_unverified")
    else:
        _check(isinstance(price, dict), "price rules")
        _check(isinstance(price.get("currency"), str) and price["currency"] in CURRENCIES, "unsupported currency")
        _check(type(price.get("currency_exponent")) is int and price["currency_exponent"] == CURRENCIES[price["currency"]], "currency exponent")
        _check(price.get("basis") in {"per_person", "per_booking"}, "pricing basis")
        _check(price.get("taxes_fees") in {"included", "unverified"}, "tax/fee treatment")
        if price["taxes_fees"] != "included":
            missing.append("taxes_fees_unverified")
        _check(price.get("payment_terms") in {"demo_full_payment", "unverified"}, "demo payment terms")
        if price["payment_terms"] != "demo_full_payment":
            missing.append("demo_payment_terms_unverified")
        _text(price.get("evidence"), "price evidence", 2000)
        if price["basis"] == "per_booking":
            _integer(price.get("amount_minor"), "booking price")
            _integer(price.get("max_guests"), "booking capacity", 1, 1000)
        else:
            bands = price.get("age_bands")
            _check(isinstance(bands, list) and 1 <= len(bands) <= 20, "price age bands")
            prior = -1
            band_ids = set()
            for band in bands:
                _check(isinstance(band, dict), "price band")
                _id(band.get("id"), "price band id")
                _check(band["id"] not in band_ids, "duplicate price band")
                band_ids.add(band["id"])
                _integer(band.get("minimum_age"), "band minimum age", 0, 120)
                _integer(band.get("maximum_age"), "band maximum age", band["minimum_age"], 120)
                _check(band["minimum_age"] > prior, "overlapping/out-of-order age bands")
                if prior >= 0 and band["minimum_age"] != prior + 1:
                    missing.append("age_price_gap")
                prior = band["maximum_age"]
                _integer(band.get("amount_minor"), "age price")
            if guest and (bands[0]["minimum_age"] != guest["minimum_age"] or bands[-1]["maximum_age"] != guest["maximum_age"]):
                missing.append("age_price_gap")
    if schedule is None:
        missing.append("schedule_unverified")
    else:
        _check(isinstance(schedule, dict), "schedule")
        _check(not set(schedule) - {"timezone", "weekdays", "slots", "evidence", "published_check_in_times", "check_in_minutes_before"}, "unsupported schedule rule")
        try:
            ZoneInfo(schedule.get("timezone", ""))
        except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise CatalogError("schedule timezone") from exc
        weekdays = schedule.get("weekdays")
        _check(isinstance(weekdays, list) and weekdays
               and all(type(d) is int and 0 <= d <= 6 for d in weekdays)
               and len(weekdays) == len(set(weekdays)), "operating weekdays")
        slots = schedule.get("slots")
        _check(isinstance(slots, list) and 1 <= len(slots) <= 30, "schedule slots")
        seen = set()
        for slot in slots:
            _check(isinstance(slot, dict), "slot")
            _id(slot.get("id"), "slot id")
            _check(slot["id"] not in seen, "duplicate slot")
            seen.add(slot["id"])
            _check(isinstance(slot.get("start"), str) and _TIME.fullmatch(slot["start"]), "slot start")
            _integer(slot.get("duration_minutes"), "slot duration", 1, 7 * 24 * 60)
        _text(schedule.get("evidence"), "schedule evidence", 2000)
        if "check_in_minutes_before" in schedule:
            _integer(schedule["check_in_minutes_before"], "check-in lead minutes", 0, 1440)
        if "published_check_in_times" in schedule:
            checkins = schedule["published_check_in_times"]
            _check(isinstance(checkins, list) and len(checkins) == len(slots), "check-in times must match departure slots in order")
            for checkin, slot in zip(checkins, slots):
                _check(isinstance(checkin, str) and _TIME.fullmatch(checkin), "check-in time")
                _check(checkin <= slot["start"], "check-in must not follow departure")
                if "check_in_minutes_before" in schedule:
                    minutes = lambda value: int(value[:2]) * 60 + int(value[3:])
                    _check(minutes(slot["start"]) - minutes(checkin) == schedule["check_in_minutes_before"], "conflicting check-in time and lead")
    options = product.get("options")
    if options is None:
        missing.append("options_unverified")
    else:
        _check(isinstance(options, list) and len(options) <= 50, "options")
        seen = set()
        for option in options:
            _check(isinstance(option, dict), "option")
            _id(option.get("id"), "option id")
            _check(option["id"] not in seen, "duplicate option")
            seen.add(option["id"])
            _text(option.get("name"), "option name", 200)
            _integer(option.get("amount_minor"), "option price")
            _integer(option.get("max_quantity"), "option maximum", 1, 1000)
            _check(option.get("basis") in {"per_person", "per_booking", "per_unit"}, "option basis")
            _check(type(option.get("required")) is bool, "option required flag")
    policies = product.get("policies")
    _check(policies is None or isinstance(policies, dict), "policies")
    if policies is None or policies.get("verified") is not True:
        missing.append("policies_unverified")
    if policies is not None:
        _check(isinstance(policies, dict), "policies")
        for key in ("cancellation", "safety", "evidence"):
            _text(policies.get(key), "policy " + key, 3000, empty=policies.get("verified") is not True)
    if product.get("pickup") is None:
        missing.append("pickup_unverified")
    else:
        pickup = product["pickup"]
        _check(isinstance(pickup, dict) and pickup.get("mode") in {"meeting_point", "included", "priced_option"}, "pickup")
        _text(pickup.get("meeting_point"), "meeting point", 1000)
        if pickup["mode"] == "priced_option":
            _check(any(o["id"] == pickup.get("option_id") for o in options or []), "pickup option missing")
    return sorted(set(missing))


def _validate_catalog(catalog):
    _check(isinstance(catalog, dict), "catalog must be an object")
    _check(catalog.get("schema_version") == SCHEMA_VERSION, "catalog schema version")
    _check(catalog.get("tenant_slug") == TENANT and catalog.get("brand") == "isluno", "catalog identity")
    _id(catalog.get("version"), "catalog version")
    products = catalog.get("products")
    _check(isinstance(products, list) and 1 <= len(products) <= MAX_PRODUCTS, "product count")
    result = copy.deepcopy(catalog)
    seen = set()
    for product in result["products"]:
        _check(isinstance(product, dict), "product")
        _id(product.get("id"), "product id")
        _check(product["id"] not in seen, "duplicate product")
        seen.add(product["id"])
        _text(product.get("name"), "product name", 200)
        _text(product.get("category"), "product category", 100)
        _text(product.get("summary"), "product summary", 3000)
        _check(type(product.get("enabled")) is bool, "product enabled flag")
        source = product.get("source")
        _check(isinstance(source, dict), "product source")
        _public_url(source.get("url"), "product source URL")
        _text(source.get("observed_at"), "observed date", 40)
        try:
            _check(datetime.fromisoformat(source["observed_at"]).tzinfo is not None, "observed date timezone")
        except ValueError as exc:
            raise CatalogError("observed date must be an ISO timestamp with timezone") from exc
        _validate_gallery(product.get("gallery"))
        inclusions = product.get("inclusions")
        _check(isinstance(inclusions, list) and len(inclusions) <= 60, "inclusions")
        for inclusion in inclusions:
            _text(inclusion, "inclusion", 500)
        source_unresolved = _validate_rules(product)
        unresolved = source_unresolved
        mode = "verified_catalog"
        demo = product.get("demo_rules")
        if demo is not None:
            _check(isinstance(demo, dict) and demo.get("authority") == "user_approved_demo_sample", "demo rule authority")
            _check(demo.get("real_booking_eligible") is False, "sample rules must be ineligible for real bookings")
            _id(demo.get("version"), "demo rule version")
            _text(demo.get("label"), "demo label", 500)
            _text(demo.get("approval_ref"), "demo approval reference", 1000)
            rules = demo.get("rules")
            _check(isinstance(rules, dict) and set(rules) == _RULE_KEYS, "complete demo rule envelope")
            effective = {**product, **rules}
            unresolved = _validate_rules(effective)
            mode = "demo_sample"
        product["readiness"] = {
            "discoverable": product["enabled"],
            "quotable": product["enabled"] and not unresolved,
            "unresolved": unresolved,
            "source_unresolved": source_unresolved,
            "pricing_mode": mode,
            "verified_images": sum(a["validation_status"] == "verified" for a in product["gallery"]),
        }
    return result


def validate_catalog(catalog):
    try:
        return _validate_catalog(catalog)
    except (TypeError, KeyError, AttributeError) as exc:
        raise CatalogError("malformed catalog field") from exc


def quote_rules(product, *, mode):
    """Explicit demo-only selector; sample values never become supplier facts."""
    _check(mode == "demo", "real supplier quotation is outside the demo adapter")
    _check(product.get("readiness", {}).get("quotable") is True, "product_not_quotable")
    if product.get("demo_rules"):
        return {"pricing_mode": "demo_sample", "label": product["demo_rules"]["label"],
                "rules": copy.deepcopy(product["demo_rules"]["rules"]), "rules_version": product["demo_rules"]["version"],
                "real_booking_eligible": False}
    return {"pricing_mode": "verified_catalog", "label": "Demo booking using catalog rules",
            "rules": copy.deepcopy({key: product[key] for key in _RULE_KEYS}), "real_booking_eligible": False}


class CatalogStore:
    """Process-safe CAS publication with immutable historical catalog snapshots."""

    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        return validate_catalog(json.loads(self.path.read_text(encoding="utf-8")))

    def snapshot(self):
        catalog = self.read()
        return {"catalog": catalog, "revision": revision(catalog)}

    def publish(self, changes, expected_revision):
        _check(isinstance(changes, list) and 1 <= len(changes) <= MAX_PRODUCTS, "product edits")
        with self.path.with_name(".isluno_catalog.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.read()
            current_revision = revision(current)
            if current_revision != expected_revision:
                raise CatalogConflict("catalog revision changed; reload before editing")
            candidate = copy.deepcopy(current)
            by_id = {p["id"]: p for p in candidate["products"]}
            edited = set()
            for change in changes:
                _check(isinstance(change, dict) and set(change) == {"id", "changes"}, "edit envelope")
                key, values = change["id"], change["changes"]
                _id(key, "edited product id")
                _check(key in by_id and key not in edited, "unknown/duplicate edited product")
                edited.add(key)
                _check(isinstance(values, dict) and values and not set(values) - _EDITABLE, "unsupported edited fields")
                # Asset validation/delivery identity is importer-owned. Editors can
                # reorder/caption/remove known images; cannot inject fetch URLs.
                if "gallery" in values:
                    known = {a["id"]: a for a in by_id[key]["gallery"]}
                    _check(isinstance(values["gallery"], list), "gallery edit")
                    for asset in values["gallery"]:
                        _check(isinstance(asset, dict) and asset.get("id") in known, "unknown gallery asset")
                        immutable = lambda a: {k: v for k, v in a.items() if k not in {"order", "caption"}}
                        _check(immutable(asset) == immutable(known[asset["id"]]), "asset source/delivery identity is immutable")
                values = copy.deepcopy(values)
                if "demo_rules" in values and values["demo_rules"] is not None:
                    _check(isinstance(values["demo_rules"], dict), "demo rule object")
                    content = {k: v for k, v in values["demo_rules"].items() if k != "version"}
                    values["demo_rules"]["version"] = "sample-" + revision(content)[:24]
                by_id[key].update(values)
            candidate["version"] = "isluno-" + revision(candidate)[:24]
            candidate = validate_catalog(candidate)
            history = self.path.with_name("isluno_catalog_versions")
            history.mkdir(mode=0o700, exist_ok=True)
            archived = history / (current_revision + ".json")
            serialized = json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            if archived.exists():
                _check(archived.read_text() == serialized, "historical snapshot integrity mismatch")
            else:
                self._atomic_write(archived, serialized)
            self._atomic_write(self.path, json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            return {"catalog": candidate, "revision": revision(candidate)}

    @staticmethod
    def _atomic_write(path, text):
        fd, name = tempfile.mkstemp(prefix=".isluno-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def validate_snapshot(snapshot):
    _check(isinstance(snapshot, dict) and set(snapshot) == {"catalog", "revision"}, "catalog snapshot envelope")
    catalog = validate_catalog(snapshot["catalog"])
    _check(revision(catalog) == snapshot["revision"], "catalog snapshot revision mismatch")
    return {"catalog": catalog, "revision": snapshot["revision"]}
