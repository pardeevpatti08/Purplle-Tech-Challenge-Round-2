from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import drop_off, fetch_store_events, fetch_store_pos, funnel_counts
from app.db import get_session
from app.store_ids import canonical_store_id

router = APIRouter(prefix="/stores", tags=["funnel"])


@router.get("/{id}/funnel")
async def store_funnel(id: str, request: Request, session: AsyncSession = Depends(get_session)):
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

    counts = funnel_counts(events, transactions)
    stages = ["ENTRY", "ZONE_VISIT", "BILLING_QUEUE", "PURCHASE"]
    response_stages = []
    previous = None
    for stage in stages:
        count = counts[stage]
        response_stages.append(
            {
                "stage": stage,
                "count": count,
                "drop_off_pct": 0.0 if previous is None else drop_off(previous, count),
            }
        )
        previous = count
    return {
        "store_id": canonical_store_id(id),
        "stages": response_stages,
    }
