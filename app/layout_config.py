import json
from functools import lru_cache
from pathlib import Path

from app.store_ids import canonical_store_id

HEATMAP_EXCLUDED_ZONE_TYPES = {"threshold", "floor", "billing", "back_area", "staff_or_storage"}


@lru_cache
def load_store_zone_types() -> dict[str, dict[str, str]]:
    path = Path("data/store_layout.json")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    stores = raw.get("stores", [raw])
    return {
        store["store_id"]: {
            zone["zone_id"]: zone.get("type", "zone").casefold()
            for zone in store.get("zones", [])
        }
        for store in stores
    }


def zones_for_store(store_id: str) -> list[str]:
    zone_types = load_store_zone_types().get(canonical_store_id(store_id), {})
    return [
        zone_id
        for zone_id, zone_type in zone_types.items()
        if zone_type not in HEATMAP_EXCLUDED_ZONE_TYPES
    ]


def is_heatmap_zone(store_id: str, zone_id: str) -> bool:
    zone_type = load_store_zone_types().get(canonical_store_id(store_id), {}).get(zone_id)
    return zone_type is None or zone_type not in HEATMAP_EXCLUDED_ZONE_TYPES


def zone_has_type(store_id: str, zone_id: str | None, *zone_types: str) -> bool:
    if not zone_id:
        return False
    zone_type = load_store_zone_types().get(canonical_store_id(store_id), {}).get(zone_id)
    return bool(zone_type and zone_type in {item.casefold() for item in zone_types})
