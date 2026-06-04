from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, get_session
from app.models import HealthResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    try:
        await session.execute(text("SELECT 1"))
        rows = (
            await session.execute(
                select(EventRecord.store_id, EventRecord.timestamp).order_by(
                    EventRecord.store_id, EventRecord.timestamp.desc()
                )
            )
        ).all()
    except Exception:
        return HealthResponse(
            status="DEGRADED",
            db_status="DOWN",
            last_event_per_store={},
            stale_feeds=[],
        )

    last_seen = {}
    for store_id, timestamp in rows:
        last_seen.setdefault(store_id, timestamp)

    now = datetime.now(timezone.utc)
    stale = [
        store_id
        for store_id, timestamp in last_seen.items()
        if timestamp is not None and now - timestamp > timedelta(minutes=10)
    ]
    return HealthResponse(
        status="OK" if not stale else "WARN",
        db_status="OK",
        last_event_per_store=last_seen,
        stale_feeds=stale,
    )
