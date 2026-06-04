from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    abandonment_rate,
    average_dwell_by_zone,
    converted_visitors,
    fetch_store_events,
    fetch_store_pos,
    latest_queue_depth,
    unique_visitors,
)
from app.db import get_session
from app.store_ids import canonical_store_id

router = APIRouter(prefix="/stores", tags=["metrics"])


@router.get("/{id}/metrics")
async def store_metrics(id: str, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        events = await fetch_store_events(session, id)
        transactions = await fetch_store_pos(session, id)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_unavailable",
                "message": "Service temporarily unavailable",
                "trace_id": getattr(request.state, "trace_id", None),
            },
        )

    visitors = unique_visitors(events)
    converted = converted_visitors(events, transactions) & visitors
    return {
        "store_id": canonical_store_id(id),
        "unique_visitors": len(visitors),
        "conversion_rate": round(len(converted) / len(visitors), 4) if visitors else 0.0,
        "avg_dwell_per_zone": average_dwell_by_zone(events),
        "queue_depth": latest_queue_depth(events),
        "abandonment_rate": abandonment_rate(events),
    }
