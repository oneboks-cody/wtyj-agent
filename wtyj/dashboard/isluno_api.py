"""Isluno routes share the host dashboard's existing authentication dependency."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query

from agents.social.isluno_itinerary import ItineraryStore
from shared.isluno_pricing import ItineraryError

from shared import isluno_config


def build_router(check_auth, itinerary_store_factory=ItineraryStore):
    router = APIRouter(prefix="/isluno", dependencies=[Depends(check_auth)])

    @router.get("/capabilities")
    def get_capabilities(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return isluno_config.capabilities()

    def read_itinerary(account_id, conversation_id, customer_ref, itinerary_id=None, revision=None, limit=50, offset=0):
        try:
            scope = isluno_config.verified_scope(account_id=account_id, conversation_id=conversation_id, customer_ref=customer_ref)
            store = itinerary_store_factory()
            return (store.get(scope, itinerary_id, revision=revision) if itinerary_id is not None
                    else {"itineraries": store.summaries(scope, limit=limit, offset=offset), "limit": limit, "offset": offset})
        except isluno_config.IslunoUnavailable as exc:
            raise HTTPException(status_code=403, detail="Isluno scope unavailable") from exc
        except ItineraryError as exc:
            status = 404 if exc.code in {"itinerary_not_found", "revision_not_found"} else 400
            raise HTTPException(status_code=status, detail={"code": exc.code, **exc.details}) from exc

    @router.get("/itineraries")
    def list_itineraries(response: Response, account_id: str, conversation_id: str, customer_ref: str,
                         limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        response.headers["Cache-Control"] = "no-store"
        return read_itinerary(account_id, conversation_id, customer_ref, limit=limit, offset=offset)

    @router.get("/itineraries/{itinerary_id}")
    def get_itinerary(response: Response, itinerary_id: str, account_id: str, conversation_id: str,
                      customer_ref: str, revision: int | None = Query(default=None, ge=1)):
        response.headers["Cache-Control"] = "no-store"
        return read_itinerary(account_id, conversation_id, customer_ref, itinerary_id, revision)

    return router
