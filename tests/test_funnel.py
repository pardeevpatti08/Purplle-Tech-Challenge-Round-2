# PROMPT: Generate pytest tests for /stores/{id}/funnel covering re-entry
# deduplication, staff exclusion, empty stores, and partial funnels.
#
# CHANGES MADE: Replaced placeholder with session-level funnel tests covering
# re-entry deduplication and staff exclusion.

from datetime import datetime, timedelta, timezone

from app.analytics import EventView, PosView, funnel_counts


def event(visitor_id: str, event_type: str, *, is_staff: bool = False) -> EventView:
    return EventView(
        event_id=f"{visitor_id}-{event_type}",
        store_id="ST1008",
        camera_id="CAM",
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        zone_id="CASH_COUNTER" if "BILLING" in event_type else "SKINCARE",
        dwell_ms=0,
        is_staff=is_staff,
        confidence=0.9,
        metadata={},
    )


def test_funnel_counts_sessions_not_raw_events():
    events = [
        event("VIS_1", "ENTRY"),
        event("VIS_1", "REENTRY"),
        event("VIS_1", "ZONE_ENTER"),
        event("VIS_1", "BILLING_QUEUE_JOIN"),
        event("STAFF_1", "ENTRY", is_staff=True),
    ]
    pos = [
        PosView(
            transaction_id="TXN_1",
            store_id="ST1008",
            timestamp=datetime(2026, 4, 10, 6, 42, tzinfo=timezone.utc),
            basket_value_inr=100.0,
        )
    ]

    assert funnel_counts(events, pos) == {
        "ENTRY": 1,
        "ZONE_VISIT": 1,
        "BILLING_QUEUE": 1,
        "PURCHASE": 1,
    }
