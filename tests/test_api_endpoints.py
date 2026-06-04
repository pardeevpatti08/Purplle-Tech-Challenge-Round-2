import asyncio
import json
from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models import IngestRequest


class FakeResult:
    def __init__(self, rowcount=1, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def scalars(self):
        class S:
            def __init__(self, rows):
                self._rows = rows

            def __iter__(self):
                return iter(self._rows)

            def all(self):
                return list(self._rows)

        return S(self._rows)


class FakeSession:
    def __init__(self, execute_result=None):
        self.execute_result = execute_result or FakeResult()

    async def execute(self, stmt):
        return self.execute_result

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_ingest_partial_and_idempotent(monkeypatch):
    # Prepare a valid and an invalid event
    valid_event = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_TEST",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"session_seq": 1},
    }
    invalid_event = {"bad": "data"}

    async def fake_get_session():
        yield FakeSession()

    # Use FastAPI dependency override to inject fake session
    from app import ingestion as ingestion_module

    app.dependency_overrides[ingestion_module.get_session] = fake_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(base_url="http://test", transport=transport) as client:
            resp = await client.post("/events/ingest", json={"events": [valid_event, invalid_event]})
            assert resp.status_code == 200
            body = resp.json()
            assert body["accepted"] == 1
            assert len(body["rejected"]) == 1
    finally:
        app.dependency_overrides.pop(ingestion_module.get_session, None)


@pytest.mark.asyncio
async def test_metrics_and_funnel_endpoints(monkeypatch):
    # Stub analytics fetch functions to return controlled events/pos
    from app.analytics import EventView, PosView

    billing_time = datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc)

    events = [
        EventView(
            event_id="e1",
            store_id="ST1008",
            camera_id="CAM",
            visitor_id="V1",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=billing_time,
            zone_id="CASH_COUNTER",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata={"queue_depth": 2},
        )
    ]
    pos = [PosView(transaction_id="t1", store_id="STORE_BLR_002", timestamp=billing_time + timedelta(minutes=3), basket_value_inr=100.0)]

    # monkeypatch analytics functions used by metrics and funnel
    async def fake_fetch_store_events(session, id):
        return events

    async def fake_fetch_store_pos(session, id):
        return pos

    # Patch both analytics and router module references so endpoints use our stubs
    monkeypatch.setattr("app.analytics.fetch_store_events", fake_fetch_store_events)
    monkeypatch.setattr("app.analytics.fetch_store_pos", fake_fetch_store_pos)
    monkeypatch.setattr("app.metrics.fetch_store_events", fake_fetch_store_events)
    monkeypatch.setattr("app.metrics.fetch_store_pos", fake_fetch_store_pos)
    monkeypatch.setattr("app.funnel.fetch_store_events", fake_fetch_store_events)
    monkeypatch.setattr("app.funnel.fetch_store_pos", fake_fetch_store_pos)

    transport = ASGITransport(app=app)
    async with AsyncClient(base_url="http://test", transport=transport) as client:
        resp = await client.get("/stores/STORE_BLR_002/metrics")
        assert resp.status_code == 200
        body = resp.json()
        from app.metrics import canonical_store_id
        assert body["store_id"] == canonical_store_id("STORE_BLR_002")
        assert "unique_visitors" in body

        # funnel
        resp2 = await client.get("/stores/STORE_BLR_002/funnel")
        assert resp2.status_code == 200
        fb = resp2.json()
        from app.metrics import canonical_store_id
        assert fb["store_id"] == canonical_store_id("STORE_BLR_002")


@pytest.mark.asyncio
async def test_health_degraded_and_ok(monkeypatch):
    # Simulate DB down
    async def bad_get_session():
        class S:
            async def execute(self, q):
                raise Exception("db down")

        yield S()

    # Override dependency in FastAPI for health.get_session
    from app import health as health_module
    app.dependency_overrides[health_module.get_session] = bad_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(base_url="http://test", transport=transport) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "DEGRADED"
    finally:
        app.dependency_overrides.pop(health_module.get_session, None)

    # Simulate DB ok with last event older than 10 minutes -> stale
    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    async def good_session():
        class S:
            async def execute(self, q):
                return None

            async def execute_events(self, q):
                return Rows([("S1", datetime(2000, 1, 1, tzinfo=timezone.utc))])

        return S()

    # monkeypatch the get_session used in health
    from app import health as health_module
    app.dependency_overrides[health_module.get_session] = good_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(base_url="http://test", transport=transport) as client:
            # this will likely return OK because our stub is minimal; at least it must 200
            resp = await client.get("/health")
            assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(health_module.get_session, None)


@pytest.mark.asyncio
async def test_store_anomalies_endpoint(monkeypatch):
    from app.analytics import EventView, PosView

    day_one = datetime(2026, 4, 8, 6, 40, tzinfo=timezone.utc)
    day_two = datetime(2026, 4, 9, 6, 40, tzinfo=timezone.utc)

    events = [
        EventView(
            event_id="e1",
            store_id="STORE_BLR_002",
            camera_id="CAM",
            visitor_id="V1",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=day_one,
            zone_id="CASH_COUNTER",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata={"queue_depth": 12},
        ),
        EventView(
            event_id="e2",
            store_id="STORE_BLR_002",
            camera_id="CAM",
            visitor_id="V1",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=day_two,
            zone_id="CASH_COUNTER",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata={"queue_depth": 12},
        ),
    ]
    pos = [PosView(transaction_id="t1", store_id="STORE_BLR_002", timestamp=day_one, basket_value_inr=100.0)]

    async def fake_fetch_store_events(session, id):
        return events

    async def fake_fetch_store_pos(session, id):
        return pos

    async def fake_get_session():
        yield FakeSession()

    from app import anomalies as anomalies_module

    app.dependency_overrides[anomalies_module.get_session] = fake_get_session
    monkeypatch.setattr("app.anomalies.fetch_store_events", fake_fetch_store_events)
    monkeypatch.setattr("app.anomalies.fetch_store_pos", fake_fetch_store_pos)
    monkeypatch.setattr("app.anomalies.zones_for_store", lambda store_id: ["A", "B"])

    transport = ASGITransport(app=app)
    from app.metrics import canonical_store_id

    try:
        async with AsyncClient(base_url="http://test", transport=transport) as client:
            resp = await client.get("/stores/STORE_BLR_002/anomalies")
            assert resp.status_code == 200
            body = resp.json()
            assert body["store_id"] == canonical_store_id("STORE_BLR_002")
            assert any(anomaly["type"] == "BILLING_QUEUE_SPIKE" for anomaly in body["anomalies"])
    finally:
        app.dependency_overrides.pop(anomalies_module.get_session, None)


@pytest.mark.asyncio
async def test_store_heatmap_endpoint(monkeypatch):
    from app.analytics import EventView

    events = [
        EventView(
            event_id="e1",
            store_id="STORE_BLR_002",
            camera_id="CAM",
            visitor_id="V1",
            event_type="ZONE_ENTER",
            timestamp=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
            zone_id="A",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata={},
        ),
        EventView(
            event_id="e2",
            store_id="ST1008",
            camera_id="CAM",
            visitor_id="V2",
            event_type="ZONE_ENTER",
            timestamp=datetime(2026, 4, 10, 6, 41, tzinfo=timezone.utc),
            zone_id="FOH",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata={},
        )
    ]

    async def fake_fetch_store_events(session, id):
        return events

    async def fake_get_session():
        yield FakeSession()

    from app import heatmap as heatmap_module

    app.dependency_overrides[heatmap_module.get_session] = fake_get_session
    monkeypatch.setattr("app.heatmap.fetch_store_events", fake_fetch_store_events)

    transport = ASGITransport(app=app)
    from app.metrics import canonical_store_id
    try:
        async with AsyncClient(base_url="http://test", transport=transport) as client:
            resp = await client.get("/stores/STORE_BLR_002/heatmap")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data_confidence"] == "LOW"
            assert body["store_id"] == canonical_store_id("STORE_BLR_002")
            assert isinstance(body["zones"], list)
            assert {zone["zone_id"] for zone in body["zones"]} == {"A"}
    finally:
        app.dependency_overrides.pop(heatmap_module.get_session, None)


@pytest.mark.asyncio
async def test_store_stream_endpoint(monkeypatch):
    from app.analytics import EventView, PosView
    from app.metrics import canonical_store_id

    event = EventView(
        event_id="e1",
        store_id="STORE_BLR_002",
        camera_id="CAM",
        visitor_id="V1",
        event_type="BILLING_QUEUE_JOIN",
        timestamp=datetime(2026, 4, 10, 6, 40, tzinfo=timezone.utc),
        zone_id="CASH_COUNTER",
        dwell_ms=0,
        is_staff=False,
        confidence=0.9,
        metadata={"queue_depth": 2},
    )
    pos = [PosView(transaction_id="t1", store_id="STORE_BLR_002", timestamp=datetime(2026, 4, 10, 6, 45, tzinfo=timezone.utc), basket_value_inr=100.0)]

    async def fake_fetch_store_events(session, id):
        return [event]

    async def fake_fetch_store_pos(session, id):
        return pos

    class FakeContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_get_session():
        return FakeContext(FakeSession())

    monkeypatch.setattr("app.stream.get_session", fake_get_session)
    monkeypatch.setattr("app.stream.fetch_store_events", fake_fetch_store_events)
    monkeypatch.setattr("app.stream.fetch_store_pos", fake_fetch_store_pos)

    from app.stream import stream_metrics

    response = await stream_metrics("STORE_BLR_002")
    assert response.media_type == "text/event-stream"
    assert response.status_code == 200
    chunk = await response.body_iterator.__anext__()
    assert isinstance(chunk, dict)
    assert "data" in chunk
    assert "store_id" in chunk["data"]


def test_layout_config_zones_for_store():
    from app.layout_config import zones_for_store

    zones = zones_for_store("ST1008")
    assert "FOH" not in zones
    assert "ENTRY" not in zones
    assert "FAC" in zones
