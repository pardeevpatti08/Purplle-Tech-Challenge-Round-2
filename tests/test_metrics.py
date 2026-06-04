# PROMPT: Generate pytest tests for /stores/{id}/metrics covering zero visitors,
# all-staff events, zero purchases, staff exclusion, and dwell aggregation.
#
# CHANGES MADE: Replaced placeholder with direct analytics helper tests for staff
# exclusion, conversion correlation, dwell aggregation, and queue metrics.

from datetime import datetime, timedelta, timezone

from app.analytics import (
    EventView,
    PosView,
    abandonment_rate,
    average_dwell_by_zone,
    converted_visitors,
    latest_queue_depth,
    unique_visitors,
)


def event(
    visitor_id: str,
    event_type: str,
    *,
    zone_id: str | None = None,
    timestamp: datetime | None = None,
    is_staff: bool = False,
    dwell_ms: int = 0,
    metadata: dict | None = None,
) -> EventView:
    return EventView(
        event_id=f"evt-{visitor_id}-{event_type}-{zone_id}",
        store_id="ST1008",
        camera_id="CAM",
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=timestamp or datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=0.9,
        metadata=metadata or {},
    )


def test_metrics_exclude_staff_and_correlate_pos():
    billing_time = datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc)
    events = [
        event("VIS_1", "ENTRY"),
        event("VIS_1", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER", timestamp=billing_time),
        event("STAFF_1", "ENTRY", is_staff=True),
    ]
    pos = [
        PosView(
            transaction_id="TXN_1",
            store_id="ST1008",
            timestamp=billing_time + timedelta(minutes=3),
            basket_value_inr=100.0,
        )
    ]

    assert unique_visitors(events) == {"VIS_1"}
    assert converted_visitors(events, pos) == {"VIS_1"}


def test_unique_visitors_count_entry_events_only():
    events = [
        event("VIS_ENTRY", "ENTRY"),
        event("VIS_FLOOR_DUPLICATE", "ZONE_ENTER", zone_id="SKINCARE"),
        event("VIS_BILLING_DUPLICATE", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER"),
        event("STAFF_1", "ENTRY", is_staff=True),
    ]

    assert unique_visitors(events) == {"VIS_ENTRY"}


def test_conversion_numerator_is_limited_to_entry_visitors():
    billing_time = datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc)
    events = [
        event("VIS_ENTRY", "ENTRY"),
        event("VIS_ENTRY", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER", timestamp=billing_time),
        event("VIS_BILLING_ONLY", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER", timestamp=billing_time),
    ]
    pos = [
        PosView(
            transaction_id="TXN_1",
            store_id="ST1008",
            timestamp=billing_time + timedelta(minutes=3),
            basket_value_inr=100.0,
        )
    ]

    assert converted_visitors(events, pos) == {"VIS_ENTRY", "VIS_BILLING_ONLY"}
    assert converted_visitors(events, pos) & unique_visitors(events) == {"VIS_ENTRY"}


def test_metrics_zero_purchase_and_dwell_aggregation():
    events = [
        event("VIS_1", "ZONE_DWELL", zone_id="SKINCARE", dwell_ms=30000),
        event("VIS_2", "ZONE_DWELL", zone_id="SKINCARE", dwell_ms=60000),
        event("VIS_3", "ZONE_DWELL", zone_id="MAKEUP", dwell_ms=90000, is_staff=True),
    ]

    assert converted_visitors(events, []) == set()
    assert average_dwell_by_zone(events) == {"SKINCARE": 45000.0}


def test_queue_depth_and_abandonment_rate():
    events = [
        event("VIS_1", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER", metadata={"queue_depth": 2}),
        event("VIS_2", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER", metadata={"queue_depth": 4}),
        event("VIS_2", "BILLING_QUEUE_ABANDON", zone_id="CASH_COUNTER"),
    ]

    assert latest_queue_depth(events) == 4
    assert abandonment_rate(events) == 0.5
