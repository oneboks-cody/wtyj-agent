"""Authenticated catalog publication, using the existing tenant and CAS boundary."""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from shared import config_loader, isluno_config
from shared.isluno_catalog import CatalogStore, CatalogError, CatalogConflict
from shared.isluno_media import MediaLibrary, MediaUnavailable


class ProductEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: StrictStr = Field(min_length=1, max_length=120)
    changes: dict[str, Any]


class Publication(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    changes: list[ProductEdit] = Field(min_length=1, max_length=500)


def catalog_store():
    return CatalogStore(Path(config_loader._CONFIG_PATH).with_name('isluno_catalog.json'))


def build_router(check_auth, store_factory=catalog_store, media_factory=MediaLibrary):
    def authorize():
        try:
            isluno_config.active_profile()
        except isluno_config.IslunoUnavailable as exc:
            raise HTTPException(403, 'Isluno catalog unavailable') from exc

    router = APIRouter(prefix='/catalog', dependencies=[Depends(check_auth), Depends(authorize)])

    @router.get('')
    def read(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        try:
            return {**store_factory().snapshot(), 'editable': True}
        except (OSError, CatalogError, ValueError) as exc:
            raise HTTPException(503, 'Catalog could not be loaded') from exc

    @router.put('')
    def publish(body: Publication, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        try:
            result = store_factory().publish([edit.model_dump() for edit in body.changes], body.expected_revision)
            return {**result, 'editable': True}
        except CatalogConflict as exc:
            raise HTTPException(409, {'code': 'catalog_conflict', 'message': str(exc)}) from exc
        except CatalogError as exc:
            raise HTTPException(422, {'code': 'invalid_catalog', 'message': str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(503, 'Catalog could not be saved; reload to check its current revision') from exc

    @router.get('/media/{digest}.jpg')
    def media(digest: str):
        try:
            return Response(media_factory().jpeg(digest), media_type='image/jpeg',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except (MediaUnavailable, OSError, CatalogError):
            raise HTTPException(404, 'Catalog image unavailable')

    return router
