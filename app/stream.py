import asyncio
import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.analytics import (
    fetch_store_events,
    fetch_store_pos,
    unique_visitors,
    converted_visitors,
    average_dwell_by_zone,
    latest_queue_depth,
    abandonment_rate,
)
from app.db import get_session
from app.store_ids import canonical_store_id

router = APIRouter()


@router.get("/stores/{id}/stream")
async def stream_metrics(id: str):
    async def event_generator():
        while True:
            async with get_session() as session:
                events = await fetch_store_events(session, id)
                pos = await fetch_store_pos(session, id)
            visitors = unique_visitors(events)
            converted = converted_visitors(events, pos)
            metrics = {
                "store_id": canonical_store_id(id),
                "unique_visitors": len(visitors),
                "conversion_rate": round(len(converted) / len(visitors), 4) if visitors else 0.0,
                "avg_dwell_per_zone": average_dwell_by_zone(events),
                "queue_depth": latest_queue_depth(events),
                "abandonment_rate": abandonment_rate(events),
            }
            yield {"data": json.dumps(metrics)}
            await asyncio.sleep(5)

    return EventSourceResponse(event_generator())
