from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, get_session
from app.models import EventIn, IngestRequest, IngestResponse, RejectedEvent, ReplaceEventsRequest


router = APIRouter(prefix="/events", tags=["events"])


def event_insert(event: EventIn):
    return (
        insert(EventRecord)
        .values(
            event_id=str(event.event_id),
            store_id=event.store_id,
            camera_id=event.camera_id,
            visitor_id=event.visitor_id,
            event_type=event.event_type.value,
            timestamp=event.timestamp,
            zone_id=event.zone_id,
            dwell_ms=event.dwell_ms,
            is_staff=event.is_staff,
            confidence=event.confidence,
            metadata_json=event.metadata.model_dump(),
        )
        .on_conflict_do_nothing(index_elements=[EventRecord.event_id])
    )


@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(
    payload: IngestRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    accepted = 0
    rejected: list[RejectedEvent] = []
    request.state.event_count = len(payload.events)

    for raw_event in payload.events:
        try:
            event = EventIn.model_validate(raw_event)
        except ValidationError as exc:
            rejected.append(RejectedEvent(event=raw_event, reason=str(exc.errors()[0]["msg"])))
            continue

        result = await session.execute(event_insert(event))
        accepted += result.rowcount or 0

    await session.commit()
    return IngestResponse(accepted=accepted, rejected=rejected)


@router.post("/replace", response_model=IngestResponse)
async def replace_events(
    payload: ReplaceEventsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    validated: list[EventIn] = []
    rejected: list[RejectedEvent] = []
    request.state.event_count = len(payload.events)

    for raw_event in payload.events:
        try:
            validated.append(EventIn.model_validate(raw_event))
        except ValidationError as exc:
            rejected.append(RejectedEvent(event=raw_event, reason=str(exc.errors()[0]["msg"])))

    if rejected:
        return IngestResponse(accepted=0, rejected=rejected)

    store_ids = {event.store_id for event in validated}
    if store_ids:
        await session.execute(delete(EventRecord).where(EventRecord.store_id.in_(store_ids)))
    for event in validated:
        await session.execute(event_insert(event))
    await session.commit()
    return IngestResponse(accepted=len(validated), rejected=[])
