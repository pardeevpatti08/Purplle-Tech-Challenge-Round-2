from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import fetch_store_events, heatmap_zones, unique_visitors
from app.db import get_session
from app.store_ids import canonical_store_id

router = APIRouter(prefix="/stores", tags=["heatmap"])


@router.get("/{id}/heatmap")
async def store_heatmap(id: str, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        events = await fetch_store_events(session, id)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_unavailable",
                "message": "Service temporarily unavailable",
                "trace_id": getattr(request.state, "trace_id", None),
            },
        )

    session_count = len(unique_visitors(events))
    return {
        "store_id": canonical_store_id(id),
        "data_confidence": "LOW" if session_count < 20 else "OK",
        "zones": heatmap_zones(events),
    }
