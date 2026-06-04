import json
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query

from app.analytics import (
    EventView,
    PosView,
    abandonment_rate,
    average_dwell_by_zone,
    conversion_rate_for_events,
    funnel_counts,
    heatmap_zones,
    latest_queue_depth,
    unique_visitors,
)
from app.layout_config import is_heatmap_zone

router = APIRouter(prefix="/replay", tags=["replay"])


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@lru_cache(maxsize=2)
def load_replay_events(path_value: str, modified_ns: int, size: int) -> list[EventView]:
    path = Path(path_value)
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        events.append(
            EventView(
                event_id=raw["event_id"],
                store_id=raw["store_id"],
                camera_id=raw["camera_id"],
                visitor_id=raw["visitor_id"],
                event_type=raw["event_type"],
                timestamp=parse_time(raw["timestamp"]),
                zone_id=raw.get("zone_id"),
                dwell_ms=int(raw.get("dwell_ms") or 0),
                is_staff=bool(raw.get("is_staff")),
                confidence=float(raw.get("confidence") or 0),
                metadata=raw.get("metadata") or {},
            )
        )
    return sorted(events, key=lambda event: event.timestamp)


def replay_events() -> list[EventView]:
    path = Path("output/events.jsonl")
    if not path.exists():
        return []
    stat = path.stat()
    return load_replay_events(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1)
def load_replay_transactions() -> list[PosView]:
    import csv
    path = Path("data/pos_transactions_normalized.csv")
    if not path.exists():
        return []
    txns = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            txns.append(
                PosView(
                    transaction_id=row["transaction_id"],
                    store_id=row["store_id"],
                    timestamp=parse_time(row["timestamp"]),
                    basket_value_inr=float(row["basket_value_inr"] or 0),
                )
            )
    return txns


@lru_cache(maxsize=2)
def load_replay_layout(path_value: str, modified_ns: int) -> dict[str, Any]:
    path = Path(path_value)
    return json.loads(path.read_text(encoding="utf-8"))


def replay_layout() -> dict[str, Any]:
    path = Path("data/store_layout.json")
    if not path.exists():
        return {"stores": []}
    return load_replay_layout(str(path), path.stat().st_mtime_ns)


def media_url(path: str) -> str:
    return f"/media/{quote(path.replace(chr(92), '/'))}"


def replay_bounds() -> tuple[datetime | None, datetime | None]:
    events = replay_events()
    if not events:
        return None, None
    return events[0].timestamp, events[-1].timestamp


def replay_cursor(speed: float) -> tuple[datetime | None, float, float]:
    start, end = replay_bounds()
    if not start or not end:
        return None, 0.0, 0.0
    duration = max((end - start).total_seconds(), 1.0)
    elapsed = (time.time() * speed) % duration
    return start + timedelta(seconds=elapsed), elapsed, duration


def store_definition(store: dict[str, Any]) -> dict[str, Any]:
    source_dir = store.get("source_dir", "")
    zones = [
        {
            "zone_id": zone["zone_id"],
            "name": zone.get("name", zone["zone_id"]),
            "type": zone.get("type", "zone"),
            "polygon": zone.get("polygon", []),
            "heatmap_enabled": is_heatmap_zone(store["store_id"], zone["zone_id"]),
        }
        for zone in store.get("zones", [])
    ]
    return {
        "store_id": store["store_id"],
        "store_name": store.get("store_name", store["store_id"]),
        "layout_url": media_url(store["layout_image"]),
        "floor_plan": store.get("floor_plan", {}),
        "zones": zones,
        "cameras": [
            {
                "camera_id": camera["camera_id"],
                "role": camera.get("role", "UNKNOWN"),
                "video_url": media_url(f"{source_dir}/{camera['filename']}"),
            }
            for camera in store.get("cameras", [])
        ],
    }


def store_snapshot(store: dict[str, Any], cursor: datetime) -> dict[str, Any]:
    store_id = store["store_id"]
    events = [
        event
        for event in replay_events()
        if event.store_id == store_id and event.timestamp <= cursor
    ]
    transactions = [
        txn
        for txn in load_replay_transactions()
        if txn.store_id == store_id
    ]
    visitors = unique_visitors(events)
    conv_rate = conversion_rate_for_events(events, transactions)
    return {
        **store_definition(store),
        "metrics": {
            "unique_visitors": len(visitors),
            "queue_depth": latest_queue_depth(events),
            "abandonment_rate": abandonment_rate(events),
            "avg_dwell_per_zone": average_dwell_by_zone(events),
            "conversion_rate": conv_rate,
        },
        "funnel": funnel_counts(events, transactions),
        "heatmap": heatmap_zones(events),
        "events_seen": len(events),
    }


@router.get("/snapshot")
async def replay_snapshot(speed: float = Query(default=1.0, ge=0.1, le=20.0)):
    cursor, elapsed, duration = replay_cursor(speed)
    layout = replay_layout()
    stores = layout.get("stores", [layout] if layout.get("store_id") else [])
    return {
        "status": "LIVE" if cursor else "NO_EVENTS",
        "cursor": cursor.isoformat().replace("+00:00", "Z") if cursor else None,
        "elapsed_seconds": round(elapsed, 3),
        "duration_seconds": round(duration, 3),
        "speed": speed,
        "stores": [store_snapshot(store, cursor) for store in stores] if cursor else [],
    }
