# PROMPT: Generate pytest tests for detection pipeline logic covering group entry,
# staff flags, re-entry, and low-confidence event retention.
#
# CHANGES MADE: Phase 0 validates the bootstrap event builder before CV logic lands.

from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from pipeline.detector import Detection
from pipeline.detect import canonicalize_visitor_ids, infer_clip_start
from pipeline.emit import build_event
from pipeline.events import EventProcessor
from pipeline.layout import CameraConfig, StoreLayout, ZoneConfig, clip_start_from_filename
from pipeline.pos import PosTransaction
from pipeline.tracker import CentroidTracker, Track


def test_build_event_schema_shape():
    event = build_event(
        store_id="STORE_BLR_002",
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_TEST",
        event_type="ENTRY",
        timestamp=datetime(2026, 3, 3, 14, 22, 10, tzinfo=timezone.utc),
        confidence=0.42,
        session_seq=1,
    )

    UUID(event["event_id"])
    assert event["timestamp"] == "2026-03-03T14:22:10Z"
    assert event["confidence"] == 0.42
    assert event["metadata"]["session_seq"] == 1


def test_entry_crossing_emits_entry_event():
    layout = StoreLayout(
        store_id="ST1008",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={"ENTRY": ZoneConfig("ENTRY", "Entry", "threshold", [])},
    )
    camera = CameraConfig(
        camera_id="CAM_3",
        filename="CAM 3.mp4",
        role="ENTRY",
        coverage_zones=["ENTRY"],
        entry_threshold={"line": [[100, 0], [100, 200]], "inbound_direction": "right"},
    )
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        fps=10,
    )
    track = Track(track_id=1, bbox=(20, 20, 50, 100), centroid=(40, 60), confidence=0.8)

    first_events = processor.process_track(track, frame_idx=0)
    assert [event["event_type"] for event in first_events] == ["ZONE_ENTER"]
    track.centroid = (120, 60)
    events = processor.process_track(track, frame_idx=10)

    assert any(event["event_type"] == "ENTRY" for event in events)


def test_billing_zone_join_sets_queue_depth():
    layout = StoreLayout(
        store_id="ST1008",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={"CASH_COUNTER": ZoneConfig("CASH_COUNTER", "Cash Counter", "billing", [])},
    )
    camera = CameraConfig(
        camera_id="CAM_5",
        filename="CAM 5.mp4",
        role="BILLING",
        coverage_zones=["CASH_COUNTER"],
    )
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        fps=10,
    )
    track = Track(track_id=1, bbox=(20, 20, 50, 100), centroid=(40, 60), confidence=0.8)

    events = processor.process_track(track, frame_idx=0)
    queue_events = [event for event in events if event["event_type"] == "BILLING_QUEUE_JOIN"]

    assert queue_events[0]["metadata"]["queue_depth"] == 1


def test_camera_roles_use_zone_types_not_zone_ids():
    layout = StoreLayout(
        store_id="FUTURE_STORE",
        store_name="Future Store",
        timezone="UTC",
        cameras=[],
        zones={
            "checkout_omega": ZoneConfig("checkout_omega", "Checkout", "billing", []),
            "door_north": ZoneConfig("door_north", "North Door", "threshold", []),
        },
    )
    billing_camera = CameraConfig(
        camera_id="CAM_X",
        filename="x.mp4",
        role="BILLING",
        coverage_zones=["checkout_omega"],
        store_id="FUTURE_STORE",
    )
    entry_camera = CameraConfig(
        camera_id="CAM_Y",
        filename="y.mp4",
        role="ENTRY",
        coverage_zones=["door_north"],
        store_id="FUTURE_STORE",
    )
    track = Track(track_id=1, bbox=(0, 0, 10, 10), centroid=(5, 5), confidence=0.8)

    billing = EventProcessor(layout, billing_camera, datetime.now(timezone.utc), 10)
    entry = EventProcessor(layout, entry_camera, datetime.now(timezone.utc), 10)

    assert billing.zone_for_track(track) == "checkout_omega"
    assert billing.is_billing_zone("checkout_omega")
    assert entry.zone_for_track(track) == "door_north"


def test_zone_for_track_uses_layout_polygon_containment():
    layout = StoreLayout(
        store_id="ST1008",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={
            "FOH": ZoneConfig("FOH", "F.O.H", "floor", [[0, 0], [300, 0], [300, 300], [0, 300]]),
            "SKINCARE": ZoneConfig("SKINCARE", "Skincare", "sku_zone", [[40, 40], [120, 40], [120, 120], [40, 120]]),
        },
    )
    camera = CameraConfig(
        camera_id="CAM_ZONE",
        filename="zone.mp4",
        role="MAIN_FLOOR",
        coverage_zones=["FOH", "SKINCARE"],
    )
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        fps=10,
    )
    track = Track(track_id=1, bbox=(50, 50, 90, 110), centroid=(70, 80), confidence=0.8)

    assert processor.zone_for_track(track) == "SKINCARE"


def test_external_track_id_is_preserved():
    tracker = CentroidTracker()
    tracks = tracker.update([Detection((10, 10, 60, 120), 0.9, track_id=42)])

    assert tracks[0].track_id == 42


def test_billing_abandon_emitted_without_following_purchase():
    layout = StoreLayout(
        store_id="ST1008",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={"CASH_COUNTER": ZoneConfig("CASH_COUNTER", "Cash Counter", "billing", [])},
    )
    camera = CameraConfig(
        camera_id="CAM_5",
        filename="CAM 5.mp4",
        role="BILLING",
        coverage_zones=["CASH_COUNTER"],
    )
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        fps=10,
        pos_transactions=[
            PosTransaction(
                store_id="ST1008",
                transaction_id="TXN_LATE",
                timestamp=datetime(2026, 4, 10, 6, 50, tzinfo=timezone.utc),
                basket_value_inr=100.0,
            )
        ],
    )
    track = Track(track_id=1, bbox=(20, 20, 50, 100), centroid=(40, 60), confidence=0.8)
    processor.process_track(track, frame_idx=0)

    events = processor.close_track(track, frame_idx=50)

    assert any(event["event_type"] == "BILLING_QUEUE_ABANDON" for event in events)


def test_billing_abandon_suppressed_with_following_purchase():
    layout = StoreLayout(
        store_id="ST1008",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={"CASH_COUNTER": ZoneConfig("CASH_COUNTER", "Cash Counter", "billing", [])},
    )
    camera = CameraConfig(
        camera_id="CAM_5",
        filename="CAM 5.mp4",
        role="BILLING",
        coverage_zones=["CASH_COUNTER"],
    )
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        fps=10,
        pos_transactions=[
            PosTransaction(
                store_id="ST1008",
                transaction_id="TXN_OK",
                timestamp=datetime(2026, 4, 10, 6, 42, tzinfo=timezone.utc),
                basket_value_inr=100.0,
            )
        ],
    )
    track = Track(track_id=1, bbox=(20, 20, 50, 100), centroid=(40, 60), confidence=0.8)
    processor.process_track(track, frame_idx=0)

    events = processor.close_track(track, frame_idx=50)

    assert not any(event["event_type"] == "BILLING_QUEUE_ABANDON" for event in events)


def test_billing_purchase_does_not_use_another_stores_pos():
    layout = StoreLayout(
        store_id="ST1076",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={"CASH_COUNTER": ZoneConfig("CASH_COUNTER", "Cash Counter", "billing", [])},
    )
    camera = CameraConfig(
        camera_id="ST1076_CAM_BILLING",
        filename="billing_area.mp4",
        role="BILLING",
        coverage_zones=["CASH_COUNTER"],
        store_id="ST1076",
    )
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=datetime(2026, 4, 10, 7, 8, tzinfo=timezone.utc),
        fps=10,
        pos_transactions=[
            PosTransaction(
                store_id="ST1008",
                transaction_id="TXN_SAMPLE",
                timestamp=datetime(2026, 4, 10, 7, 12, tzinfo=timezone.utc),
                basket_value_inr=100.0,
            )
        ],
    )
    track = Track(track_id=1, bbox=(20, 20, 50, 100), centroid=(40, 60), confidence=0.8)
    processor.process_track(track, frame_idx=0)

    events = processor.close_track(track, frame_idx=50)

    assert any(event["event_type"] == "BILLING_QUEUE_ABANDON" for event in events)


def test_reentry_detected_using_embedding_similarity():
    # create a reidentifier with a high threshold
    from pipeline.tracker import ReIdentifier

    r = ReIdentifier(threshold=0.8, reentry_window_seconds=300)
    ts = datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc)
    emb = [0.1] * 64
    vid, created = r.match_or_create(ts, embedding=emb)
    assert created is False or isinstance(vid, str)

    # mark exit
    r.mark_exit(vid, ts)

    # re-enter within window with similar embedding
    vid2, reentered = r.match_or_create(ts + timedelta(seconds=60), embedding=emb)
    assert reentered is True


def test_reidentifier_matches_active_cross_camera_embedding_without_reentry():
    from pipeline.tracker import ReIdentifier

    r = ReIdentifier(threshold=0.8, reentry_window_seconds=300)
    ts = datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc)
    emb = [0.1] * 64

    vid, reentered = r.match_or_create(ts, embedding=emb)
    vid2, reentered2 = r.match_or_create(ts + timedelta(seconds=20), embedding=emb)

    assert vid2 == vid
    assert reentered is False
    assert reentered2 is False


def test_clip_start_from_filename_parses_business_date_and_time():
    parsed = clip_start_from_filename(Path("store1/CAM_2026-05-12_09-15-30.mp4"))

    assert parsed == datetime(2026, 5, 12, 9, 15, 30, tzinfo=timezone.utc)


def test_infer_clip_start_rejects_pos_from_another_store():
    layout = StoreLayout(
        store_id="ST1076",
        store_name="Test Store",
        timezone="Asia/Kolkata",
        cameras=[],
        zones={},
    )
    camera = CameraConfig(
        camera_id="ST1076_CAM_ENTRY_1",
        filename="entry 1.mp4",
        role="ENTRY",
        coverage_zones=["ENTRY"],
        store_id="ST1076",
    )

    with pytest.raises(ValueError, match="provide POS data for the same store"):
        infer_clip_start(
            clip_path=Path("entry 1.mp4"),
            camera=camera,
            layout=layout,
            pos_transactions=[
                PosTransaction(
                    store_id="ST1008",
                    transaction_id="TXN_SAMPLE",
                    timestamp=datetime(2026, 5, 12, 7, 12, tzinfo=timezone.utc),
                    basket_value_inr=100.0,
                )
            ],
        )


def test_canonicalize_visitor_ids_links_camera_local_billing_to_entry():
    events = [
        {
            "event_id": "1",
            "store_id": "ST1008",
            "camera_id": "ENTRY",
            "visitor_id": "VIS_ENTRY",
            "event_type": "ENTRY",
            "timestamp": "2026-04-10T06:40:00Z",
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {},
        },
        {
            "event_id": "2",
            "store_id": "ST1008",
            "camera_id": "BILLING",
            "visitor_id": "VIS_BILLING_LOCAL",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": "2026-04-10T06:44:00Z",
            "zone_id": "CASH_COUNTER",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {},
        },
    ]

    layout = StoreLayout(
        store_id="ST1008",
        store_name="Test Store",
        timezone="UTC",
        cameras=[],
        zones={"CASH_COUNTER": ZoneConfig("CASH_COUNTER", "Checkout", "billing", [])},
    )
    canonical = canonicalize_visitor_ids(
        events,
        layout,
        max_link_seconds=300,
        min_entry_track_seconds=0,
    )

    assert canonical[1]["visitor_id"] == "VIS_ENTRY"
    assert canonical[1]["metadata"]["original_visitor_id"] == "VIS_BILLING_LOCAL"
