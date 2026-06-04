from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, PosTransaction
from app.layout_config import is_heatmap_zone, zone_has_type
from app.store_ids import canonical_store_id


@dataclass(frozen=True)
class EventView:
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: str | None
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PosView:
    transaction_id: str
    store_id: str
    timestamp: datetime
    basket_value_inr: float


def as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_billing_event(event: EventView) -> bool:
    return event.event_type in {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"} or zone_has_type(
        event.store_id, event.zone_id, "billing"
    )


async def fetch_store_events(session: AsyncSession, store_id: str) -> list[EventView]:
    canonical = canonical_store_id(store_id)
    rows = (
        await session.execute(
            select(EventRecord)
            .where(EventRecord.store_id == canonical)
            .order_by(EventRecord.timestamp.asc(), EventRecord.event_id.asc())
        )
    ).scalars()
    return [
        EventView(
            event_id=row.event_id,
            store_id=row.store_id,
            camera_id=row.camera_id,
            visitor_id=row.visitor_id,
            event_type=row.event_type,
            timestamp=as_aware(row.timestamp),
            zone_id=row.zone_id,
            dwell_ms=row.dwell_ms or 0,
            is_staff=bool(row.is_staff),
            confidence=float(row.confidence),
            metadata=row.metadata_json or {},
        )
        for row in rows
    ]


async def fetch_store_pos(session: AsyncSession, store_id: str) -> list[PosView]:
    canonical = canonical_store_id(store_id)
    rows = (
        await session.execute(
            select(PosTransaction)
            .where(PosTransaction.store_id == canonical)
            .order_by(PosTransaction.timestamp.asc())
        )
    ).scalars()
    return [
        PosView(
            transaction_id=row.transaction_id,
            store_id=row.store_id,
            timestamp=as_aware(row.timestamp),
            basket_value_inr=float(row.basket_value_inr or Decimal("0")),
        )
        for row in rows
    ]


def non_staff(events: list[EventView]) -> list[EventView]:
    return [event for event in events if not event.is_staff]


def unique_visitors(events: list[EventView]) -> set[str]:
    return {
        event.visitor_id
        for event in non_staff(events)
        if event.event_type == "ENTRY"
    }


def converted_visitors(events: list[EventView], transactions: list[PosView]) -> set[str]:
    converted: set[str] = set()
    billing_events = [event for event in non_staff(events) if is_billing_event(event)]
    for event in billing_events:
        window_end = event.timestamp + timedelta(minutes=5)
        if any(
            canonical_store_id(event.store_id) == canonical_store_id(txn.store_id)
            and event.timestamp <= txn.timestamp <= window_end
            for txn in transactions
        ):
            converted.add(event.visitor_id)
    return converted


def average_dwell_by_zone(events: list[EventView]) -> dict[str, float]:
    totals: dict[str, list[int]] = {}
    for event in non_staff(events):
        if event.event_type != "ZONE_DWELL" or not event.zone_id:
            continue
        totals.setdefault(event.zone_id, []).append(event.dwell_ms)
    return {
        zone_id: round(sum(values) / len(values), 2)
        for zone_id, values in totals.items()
        if values
    }


def latest_queue_depth(events: list[EventView]) -> int:
    joins = [
        event
        for event in non_staff(events)
        if event.event_type == "BILLING_QUEUE_JOIN"
    ]
    if not joins:
        return 0
    value = joins[-1].metadata.get("queue_depth")
    return int(value or 0)


def abandonment_rate(events: list[EventView]) -> float:
    joins = sum(1 for event in non_staff(events) if event.event_type == "BILLING_QUEUE_JOIN")
    abandons = sum(1 for event in non_staff(events) if event.event_type == "BILLING_QUEUE_ABANDON")
    if joins == 0:
        return 0.0
    return round(abandons / joins, 4)


def funnel_counts(events: list[EventView], transactions: list[PosView]) -> dict[str, int]:
    customer_events = non_staff(events)
    entry = {
        event.visitor_id
        for event in customer_events
        if event.event_type in {"ENTRY", "REENTRY"}
    }
    zone_visit = {
        event.visitor_id
        for event in customer_events
        if event.event_type in {"ZONE_ENTER", "ZONE_DWELL"} and event.zone_id
    }
    billing = {
        event.visitor_id
        for event in customer_events
        if event.event_type == "BILLING_QUEUE_JOIN" or is_billing_event(event)
    }
    purchase = converted_visitors(customer_events, transactions) & entry
    if not entry:
        entry = unique_visitors(customer_events)
    return {
        "ENTRY": len(entry),
        "ZONE_VISIT": len(zone_visit),
        "BILLING_QUEUE": len(billing),
        "PURCHASE": len(purchase),
    }


def drop_off(previous: int, current: int) -> float:
    if previous <= 0:
        return 0.0
    return round(max(previous - current, 0) / previous * 100, 2)


def heatmap_zones(events: list[EventView]) -> list[dict[str, Any]]:
    by_zone: dict[str, dict[str, Any]] = {}
    for event in non_staff(events):
        if not event.zone_id or event.event_type not in {"ZONE_ENTER", "ZONE_DWELL"}:
            continue
        if not is_heatmap_zone(event.store_id, event.zone_id):
            continue
        zone = by_zone.setdefault(event.zone_id, {"visit_count": 0, "dwell_values": []})
        if event.event_type == "ZONE_ENTER":
            zone["visit_count"] += 1
        if event.event_type == "ZONE_DWELL":
            zone["dwell_values"].append(event.dwell_ms)

    max_visits = max((zone["visit_count"] for zone in by_zone.values()), default=0)
    result = []
    for zone_id, values in sorted(by_zone.items()):
        dwell_values = values["dwell_values"]
        result.append(
            {
                "zone_id": zone_id,
                "visit_count": values["visit_count"],
                "avg_dwell_ms": round(sum(dwell_values) / len(dwell_values), 2) if dwell_values else 0.0,
                "heat_score": round(values["visit_count"] / max_visits * 100, 2) if max_visits else 0.0,
            }
        )
    return result


def conversion_rate_for_events(events: list[EventView], transactions: list[PosView]) -> float:
    visitors = unique_visitors(events)
    if not visitors:
        return 0.0
    return len(converted_visitors(events, transactions) & visitors) / len(visitors)


def daily_conversion_rates(events: list[EventView], transactions: list[PosView]) -> dict[Any, float]:
    rates = {}
    for day in sorted({event.timestamp.date() for event in events}):
        day_events = [event for event in events if event.timestamp.date() == day]
        day_transactions = [txn for txn in transactions if txn.timestamp.date() == day]
        rates[day] = conversion_rate_for_events(day_events, day_transactions)
    return rates
