"""Isluno routes share the host dashboard's existing authentication dependency."""
from fastapi import APIRouter, Depends, Response

from shared import isluno_config


def build_router(check_auth):
    router = APIRouter(prefix="/isluno", dependencies=[Depends(check_auth)])

    @router.get("/capabilities")
    def get_capabilities(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return isluno_config.capabilities()

    return router
