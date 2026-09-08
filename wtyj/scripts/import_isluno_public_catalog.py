"""Bounded, explicit public-site import. No supplier APIs or booking submissions.

Requires beautifulsoup4 and Pillow for import-time parsing/media validation.
Run with --fetch-public-site to authorize public GETs for this invocation.
Runtime catalog reads never run this importer or depend on these expiring URLs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "wtyj"))
from shared.isluno_catalog import validate_catalog

MAX_PAGES = 120
MAX_REQUESTS = 700
MAX_TOTAL_BYTES = 150 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 24_000_000


def canonical(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def allowed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.port in (None, 443)
            and (parsed.hostname == "isluno.com" or
                 (parsed.hostname == "storage.googleapis.com" and parsed.path.startswith("/isluno-bucket/assets/"))))


class PublicOnlyRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError("redirect outside public Isluno source allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self):
        self.requests = 0
        self.bytes = 0
        self.reserved = 0
        self.lock = threading.Lock()

    def get(self, url, limit):
        if not allowed(url):
            raise ValueError("URL outside public Isluno source allowlist")
        with self.lock:
            if self.requests >= MAX_REQUESTS or self.bytes + self.reserved + limit > MAX_TOTAL_BYTES:
                raise ValueError("public import request/byte ceiling reached")
            self.requests += 1
            self.reserved += limit
        consumed = 0
        try:
            request = Request(url, headers={"User-Agent": "Isluno-catalog-import/1.0 (read-only)", "Accept-Encoding": "identity"})
            with build_opener(PublicOnlyRedirects()).open(request, timeout=20) as response:
                data = response.read(limit + 1)
                consumed = len(data)
                if consumed > limit:
                    raise ValueError("public source exceeds byte limit")
                return data
        finally:
            with self.lock:
                self.reserved -= limit
                self.bytes += consumed


def words(text, maximum):
    return " ".join(text.split()[:maximum])


def extract_product(html, url, observed_at):
    """Parse only product content; never import reviews, scripts or page prompts."""
    page = BeautifulSoup(html, "html.parser")
    heading = page.select_one("h1.pages-tour-title")
    if heading is None:
        raise ValueError("missing product heading")
    sections = {}
    for title in page.select(".pages-tour-main section h2"):
        sections[title.get_text(" ", strip=True)] = title.parent
    overview = sections.get("Overview")
    includes = sections.get("What's included")
    additional = sections.get("Additional Information")
    summary = words(" ".join(p.get_text(" ", strip=True) for p in overview.select("p")), 70) if overview else ""
    inclusions = []
    allowance = 50
    for item in includes.select("li") if includes else []:
        value = words(item.get_text(" ", strip=True), allowance)
        if value:
            inclusions.append(value)
            allowance -= len(value.split())
    category = page.select_one('.pages-tour-metadata a[href*="category="]')
    if category is None:
        category = page.select_one('.pages-tour-metadata a')
    price = page.select_one(".tour-sidebar-amount")
    metadata = [words(e.get_text(" ", strip=True), 12) for e in page.select(".pages-tour-info-item")]
    name = heading.get_text(" ", strip=True)
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    assets, seen, duplicates = [], set(), 0
    for img in page.select('img[alt^="Gallery image"]'):
        signed_url = img.get("src", "")
        stable_url = canonical(signed_url)
        if stable_url in seen:
            duplicates += 1
            continue
        seen.add(stable_url)
        assets.append({"id": "img-" + hashlib.sha256(stable_url.encode()).hexdigest()[:24],
                       "source_url": stable_url, "_fetch_url": signed_url,
                       "caption": name, "order": len(assets), "validation_status": "missing"})
    return {
        "id": slug, "name": name, "category": category.get_text(" ", strip=True) if category else "Unclassified",
        "summary": summary, "enabled": True,
        "source": {"url": url, "observed_at": observed_at, "fixture_only": False,
                   "content_sha256": hashlib.sha256(html).hexdigest(), "kind": "public_listing"},
        "supplier_ref": None, "gallery": assets, "schedule": None, "guest_rules": None,
        "price_rules": None, "options": None, "pickup": None, "policies": None,
        "inclusions": inclusions,
        "source_claims": {
            "advertised_from": price.get_text(" ", strip=True) if price else None,
            "metadata": metadata,
            "additional_information": words(" ".join(e.get_text(" ", strip=True) for e in additional.select("li")), 20) if additional else "",
            "guarantees": words(" ".join(e.get_text(" ", strip=True) for e in page.select(".tour-sidebar-guarantee-text")), 30),
            "disposition": "Listing claims support discovery only. Exact currency/age prices, schedule, extras, pickup and provider policies require verification before quotation.",
        },
        "import_notes": {"duplicate_gallery_urls": duplicates, "supplier_mapping": "unverified"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-public-site", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "clients/mermaid/config/isluno_catalog.json")
    args = parser.parse_args()
    if not args.fetch_public_site:
        parser.error("Public GETs require --fetch-public-site; the importer never runs at runtime.")
    if args.output.exists():
        parser.error("Refusing to overwrite an existing catalog; import to a new staging path and reconcile edits.")
    fetcher = Fetcher()
    observed_at = datetime.now(timezone.utc).isoformat()
    listing_urls = ["https://isluno.com/", "https://isluno.com/explore"]
    products_seen = set()
    listing_evidence = []
    for url in listing_urls:
        data = fetcher.get(url, 2 * 1024 * 1024)
        page = BeautifulSoup(data, "html.parser")
        urls = {urljoin(url, a["href"]).split("?")[0].rstrip("/") for a in page.select('a[href^="/tours/"]')}
        products_seen.update(urls)
        listing_evidence.append({"url": url, "sha256": hashlib.sha256(data).hexdigest(), "product_urls": sorted(urls)})
    if not 1 <= len(products_seen) <= MAX_PAGES:
        raise ValueError("product inventory exceeds import bounds or is empty")
    products, page_errors = [], []
    for index, url in enumerate(sorted(products_seen)):
        try:
            products.append(extract_product(fetcher.get(url, 2 * 1024 * 1024), url, observed_at))
        except Exception as exc:
            page_errors.append({"url": url, "error_type": type(exc).__name__})
        if (index + 1) % 10 == 0:
            print(f"Read {index + 1}/{len(products_seen)} product pages", flush=True)
    if page_errors:
        raise ValueError("Incomplete product import: " + json.dumps(page_errors))
    asset_dir = ROOT / "wtyj/assets/isluno"
    asset_dir.mkdir(parents=True, exist_ok=True)
    sources = {a["source_url"]: a["_fetch_url"] for p in products for a in p["gallery"]}

    def ingest(entry):
        stable_url, signed_url = entry
        try:
            data = fetcher.get(signed_url, MAX_IMAGE_BYTES)
            with Image.open(BytesIO(data)) as decoded:
                kind = (decoded.format or "").lower()
                if kind not in {"webp", "jpeg", "png"}:
                    raise ValueError("unsupported image type")
                width, height = decoded.size
                if not 0 < width <= 12000 or not 0 < height <= 12000:
                    raise ValueError("unsupported image dimensions")
                decoded.verify()
            digest = hashlib.sha256(data).hexdigest()
            filename = digest + "." + kind
            target = asset_dir / filename
            if target.exists() and target.read_bytes() != data:
                raise ValueError("asset hash collision")
            if not target.exists():
                target.write_bytes(data)
            return stable_url, {"validation_status": "verified", "sha256": digest, "format": kind,
                                "width": width, "height": height, "bytes": len(data),
                                "delivery_path": "assets/isluno/" + filename, "delivery_url": None}
        except Exception as exc:
            # Do not serialize exception URLs: they may contain temporary tokens.
            return stable_url, {"validation_status": "missing", "error_type": type(exc).__name__}

    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, (url, value) in enumerate(pool.map(ingest, sorted(sources.items()))):
            results[url] = value
            if (index + 1) % 25 == 0:
                print(f"Validated {index + 1}/{len(sources)} source images", flush=True)
    duplicates = []
    for product in products:
        seen = set()
        gallery = []
        for asset in product["gallery"]:
            asset.pop("_fetch_url")
            asset.update(results[asset["source_url"]])
            digest = asset.get("sha256")
            if digest and digest in seen:
                duplicates.append({"product_id": product["id"], "source_url": asset["source_url"], "sha256": digest})
                continue
            seen.add(digest)
            asset["order"] = len(gallery)
            gallery.append(asset)
        product["gallery"] = gallery
    catalog = validate_catalog({"schema_version": "isluno.v1", "tenant_slug": "mermaid", "brand": "isluno",
                                "version": "isluno-public-" + observed_at[:10], "products": products})
    products = catalog["products"]
    report = {"schema_version": "isluno.inventory.v1", "observed_at": observed_at,
              "historical_product_count": 31, "discovered_product_count": len(products_seen), "imported_product_count": len(products),
              "count_delta": len(products_seen) - 31, "listing_evidence": listing_evidence,
              "coverage_limit": "Every distinct public product URL linked by the unfiltered explore and homepage at capture time; not proof of unpublished supplier/owner inventory.",
              "gallery_source_count": len(sources), "gallery_associations": sum(len(p["gallery"]) for p in products),
              "unique_image_bytes": sum((asset_dir / p).stat().st_size for p in {Path(a["delivery_path"]).name for product in products for a in product["gallery"] if a["validation_status"] == "verified"}),
              "missing_assets": [{"source_url": url, **result} for url, result in results.items() if result["validation_status"] != "verified"],
              "same_content_duplicates": duplicates,
              "products": [{"id": p["id"], "source_url": p["source"]["url"], "gallery_count": len(p["gallery"]), "readiness": p["readiness"]} for p in products],
              "public_get_requests": fetcher.requests, "public_bytes_read": fetcher.bytes,
              "supplier_api_calls": 0, "provider_calls": 0, "image_policy": "Original approved public trip image bytes, content-addressed and validated; short-lived URL query signatures omitted. Hosting/delivery is a later authorized stage."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    report_path = args.output.with_name("isluno_inventory_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ["imported_product_count", "gallery_source_count", "gallery_associations", "unique_image_bytes", "public_get_requests"]}))
    print("Missing assets:", len(report["missing_assets"]), "Quote-ready products:", sum(p["readiness"]["quotable"] for p in products))


if __name__ == "__main__":
    main()
