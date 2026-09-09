"""Serve only catalog-approved originals as bounded WhatsApp JPEGs."""
from io import BytesIO
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from PIL import Image, ImageOps
from fastapi import APIRouter, HTTPException, Response
from shared import config_loader
from shared.isluno_catalog import CatalogStore
from shared.isluno_config import active_profile, IslunoUnavailable


class MediaUnavailable(ValueError):
    pass


class MediaLibrary:
    def __init__(self, catalog_path=None, asset_root=None, public_base=None):
        self.catalog_path = Path(catalog_path) if catalog_path else Path(config_loader._CONFIG_PATH).with_name('isluno_catalog.json')
        self.asset_root = Path(asset_root) if asset_root else Path(__file__).resolve().parents[1] / 'assets/isluno'
        self.public_base = public_base if public_base is not None else (config_loader.get_raw().get('isluno') or {}).get('media_base_url', '')

    def jpeg(self, digest):
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise MediaUnavailable('invalid_media_id')
        catalog = CatalogStore(self.catalog_path).read()
        asset = next((a for p in catalog['products'] for a in p['gallery']
                      if a.get('sha256') == digest and a['validation_status'] == 'verified'), None)
        if asset is None:
            try:
                welcome = active_profile().get('welcome_media', {})
            except IslunoUnavailable as exc:
                raise MediaUnavailable('media_not_in_catalog') from exc
            if welcome.get('sha256') != digest:
                raise MediaUnavailable('media_not_approved')
            asset = welcome
            path = self.asset_root / 'brand' / (digest + '.' + asset['format'])
        else:
            path = self.asset_root / (digest + '.' + asset['format'])
        try:
            if path.is_symlink() or path.stat().st_size != asset['bytes']:
                raise MediaUnavailable('media_integrity_failed')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise MediaUnavailable('media_integrity_failed')
            with Image.open(BytesIO(raw)) as original:
                if original.size != (asset['width'], asset['height']) or original.width * original.height > 24000000:
                    raise MediaUnavailable('media_dimensions_failed')
                original.load()
                picture = ImageOps.exif_transpose(original).convert('RGB')
                picture.thumbnail((1600, 1600))
                output = BytesIO()
                picture.save(output, format='JPEG', quality=85, optimize=True)
                result = output.getvalue()
                if len(result) > 5 * 1024 * 1024:
                    raise MediaUnavailable('media_too_large')
                return result
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise MediaUnavailable('media_unavailable') from exc

    def url(self, asset):
        base = self.public_base
        parsed = urlsplit(base) if isinstance(base, str) else None
        if (not parsed or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise MediaUnavailable('media_delivery_base_unconfigured')
        self.jpeg(asset.get('sha256'))  # Preflight stored bytes; no remote fetch.
        return base.rstrip('/') + '/' + asset['sha256'] + '.jpg'

    def welcome_url(self):
        asset = active_profile().get('welcome_media')
        if not isinstance(asset, dict):
            raise MediaUnavailable('welcome_media_unconfigured')
        return self.url(asset), asset['sha256']


def build_public_router(library_factory=MediaLibrary):
    router = APIRouter(prefix='/isluno/media')

    @router.get('/{digest}.jpg')
    def media(digest: str):
        try:
            active_profile()
            data = library_factory().jpeg(digest)
            return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff'})
        except (IslunoUnavailable, MediaUnavailable):
            raise HTTPException(404, detail='Media unavailable')
    return router
