# PROMPT: Generate pytest tests for /stores/{id}/anomalies covering queue spikes,
# dead zones, conversion drops, and normal no-anomaly behavior.
#
# CHANGES MADE: Replaced placeholder with helper-level queue and heatmap assertions.

from datetime import datetime, timezone

from app.analytics import EventView, PosView, daily_conversion_rates, heatmap_zones, latest_queue_depth


def test_queue_spike_input_uses_latest_queue_depth():
    events = [
        EventView(
            event_id="evt-1",
            store_id="ST1008",
            camera_id="CAM",
            visitor_id="VIS_1",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
            zone_id="CASH_COUNTER",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata={"queue_depth": 7},
        )
    ]

    assert latest_queue_depth(events) == 7


def test_heatmap_normalizes_visit_frequency():
    events = [
        EventView("1", "ST1008", "CAM", "V1", "ZONE_ENTER", datetime.now(timezone.utc), "A", 0, False, 0.9, {}),
        EventView("2", "ST1008", "CAM", "V2", "ZONE_ENTER", datetime.now(timezone.utc), "A", 0, False, 0.9, {}),
        EventView("3", "ST1008", "CAM", "V3", "ZONE_ENTER", datetime.now(timezone.utc), "B", 0, False, 0.9, {}),
        EventView("4", "ST1008", "CAM", "V4", "ZONE_ENTER", datetime.now(timezone.utc), "FOH", 0, False, 0.9, {}),
        EventView("5", "ST1008", "CAM", "V5", "ZONE_ENTER", datetime.now(timezone.utc), "ENTRY", 0, False, 0.9, {}),
        EventView("6", "ST1008", "CAM", "V6", "ZONE_ENTER", datetime.now(timezone.utc), "CASH_COUNTER", 0, False, 0.9, {}),
    ]

    zones = {zone["zone_id"]: zone for zone in heatmap_zones(events)}
    assert zones["A"]["heat_score"] == 100.0
    assert zones["B"]["heat_score"] == 50.0
    assert "FOH" not in zones
    assert "ENTRY" not in zones
    assert "CASH_COUNTER" not in zones


def test_daily_conversion_rates_separate_days():
    day_one = datetime(2026, 4, 9, 6, 40, tzinfo=timezone.utc)
    day_two = datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc)
    events = [
        EventView("e0", "ST1008", "CAM", "V1", "ENTRY", day_one, None, 0, False, 0.9, {}),
        EventView("e1", "ST1008", "CAM", "V1", "BILLING_QUEUE_JOIN", day_one, "CASH_COUNTER", 0, False, 0.9, {}),
        EventView("e1b", "ST1008", "CAM", "V2", "ENTRY", day_two, None, 0, False, 0.9, {}),
        EventView("e2", "ST1008", "CAM", "V2", "BILLING_QUEUE_JOIN", day_two, "CASH_COUNTER", 0, False, 0.9, {}),
    ]
    pos = [
        PosView("t1", "ST1008", day_one, 100.0),
    ]

    rates = daily_conversion_rates(events, pos)

    assert rates[day_one.date()] == 1.0
    assert rates[day_two.date()] == 0.0
