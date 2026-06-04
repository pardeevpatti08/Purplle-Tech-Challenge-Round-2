import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CameraConfig:
    camera_id: str
    filename: str
    role: str
    coverage_zones: list[str]
    entry_threshold: dict[str, Any] | None = None
    store_id: str | None = None
    source_dir: str | None = None
    clip_start: datetime | None = None
    staff_hsv_ranges: list[list[int]] | None = None
    entry_mode: str = "threshold"
    entry_confirmation_frames: int = 3
    entry_group: str | None = None


@dataclass(frozen=True)
class ZoneConfig:
    zone_id: str
    name: str
    type: str
    polygon: list[list[int]]


@dataclass(frozen=True)
class StoreLayout:
    store_id: str
    store_name: str
    timezone: str
    cameras: list[CameraConfig]
    zones: dict[str, ZoneConfig]
    clip_start: datetime | None = None
    store_aliases: dict[str, str] | None = None

    def camera_for_file(self, clip_path: Path) -> CameraConfig | None:
        normalized = clip_path.name.lower()
        parent = clip_path.parent.name.lower()
        for camera in self.cameras:
            if camera.filename.lower() == normalized and (
                camera.source_dir is None or camera.source_dir.lower() == parent
            ):
                return camera
        return None

    def canonical_store_id(self, store_id: str) -> str:
        return (self.store_aliases or {}).get(store_id.casefold(), store_id)

    def zone(self, store_id: str, zone_id: str | None) -> ZoneConfig | None:
        if not zone_id:
            return None
        canonical = self.canonical_store_id(store_id)
        return self.zones.get(f"{canonical}:{zone_id}") or self.zones.get(zone_id)

    def zone_name(self, store_id: str, zone_id: str | None) -> str | None:
        if not zone_id:
            return None
        zone = self.zone(store_id, zone_id)
        return zone.name if zone else zone_id

    def zone_has_type(self, store_id: str, zone_id: str | None, *zone_types: str) -> bool:
        zone = self.zone(store_id, zone_id)
        return bool(zone and zone.type.casefold() in {item.casefold() for item in zone_types})

    def coverage_zone_for_type(self, camera: CameraConfig, *zone_types: str) -> str | None:
        store_id = camera.store_id or self.store_id
        return next(
            (
                zone_id
                for zone_id in camera.coverage_zones
                if self.zone_has_type(store_id, zone_id, *zone_types)
            ),
            None,
        )

    def zone_for_point(self, store_id: str, zone_ids: list[str], point: tuple[int, int]) -> str | None:
        try:
            from shapely.geometry import Point, Polygon
        except Exception:
            return None

        candidates: list[ZoneConfig] = []
        for zone_id in zone_ids:
            zone = self.zones.get(f"{store_id}:{zone_id}") or self.zones.get(zone_id)
            if zone and len(zone.polygon) >= 3:
                candidates.append(zone)

        point_geom = Point(point)
        matches: list[tuple[float, ZoneConfig]] = []
        for zone in candidates:
            polygon = Polygon(zone.polygon)
            if polygon.is_valid and (polygon.contains(point_geom) or polygon.touches(point_geom)):
                matches.append((polygon.area, zone))

        if not matches:
            return None
        return min(matches, key=lambda item: item[0])[1].zone_id


def parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_layout_clip_start(raw: dict[str, Any]) -> datetime | None:
    clip_start = parse_utc_datetime(raw.get("clip_start") or raw.get("start_time"))
    if clip_start:
        return clip_start

    business_date = raw.get("business_date") or raw.get("date")
    if not business_date:
        return None

    parsed_date = date.fromisoformat(str(business_date))
    start_time = raw.get("clip_start_time") or raw.get("opening_time") or "00:00:00"
    parsed_time = time.fromisoformat(str(start_time))
    return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)


def clip_start_from_filename(clip_path: Path) -> datetime | None:
    stem = clip_path.stem
    patterns = [
        r"(?P<y>\d{4})[-_](?P<m>\d{2})[-_](?P<d>\d{2})[ T_-]?(?P<h>\d{2})?[-_:]?(?P<min>\d{2})?[-_:]?(?P<s>\d{2})?",
        r"(?P<d>\d{2})[-_](?P<m>\d{2})[-_](?P<y>\d{4})[ T_-]?(?P<h>\d{2})?[-_:]?(?P<min>\d{2})?[-_:]?(?P<s>\d{2})?",
    ]
    for pattern in patterns:
        match = re.search(pattern, stem)
        if not match:
            continue
        groups = match.groupdict()
        parsed_date = date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
        parsed_time = time(
            int(groups["h"] or 0),
            int(groups["min"] or 0),
            int(groups["s"] or 0),
        )
        return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)
    return None


def load_layout(path: str | Path) -> StoreLayout:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if "stores" in raw:
        cameras: list[CameraConfig] = []
        zones: dict[str, ZoneConfig] = {}
        default_clip_start = parse_layout_clip_start(raw)
        default_staff_ranges = raw.get("staff_hsv_ranges", [])
        aliases: dict[str, str] = {}
        for store in raw["stores"]:
            store_id = store["store_id"]
            for alias in [store_id, store.get("store_code"), *store.get("aliases", [])]:
                if alias:
                    aliases[str(alias).casefold()] = store_id
            source_dir = store.get("source_dir")
            store_clip_start = parse_layout_clip_start(store) or default_clip_start
            store_staff_ranges = store.get("staff_hsv_ranges", default_staff_ranges)
            for item in store.get("cameras", []):
                cameras.append(
                    CameraConfig(
                        camera_id=item["camera_id"],
                        filename=item["filename"],
                        role=item.get("role", "UNKNOWN"),
                        coverage_zones=item.get("coverage_zones", []),
                        entry_threshold=item.get("entry_threshold"),
                        store_id=store_id,
                        source_dir=source_dir,
                        clip_start=parse_layout_clip_start(item) or store_clip_start,
                        staff_hsv_ranges=item.get("staff_hsv_ranges", store_staff_ranges),
                        entry_mode=item.get("entry_mode", "threshold"),
                        entry_confirmation_frames=int(item.get("entry_confirmation_frames", 3)),
                        entry_group=item.get("entry_group"),
                    )
                )
            for item in store.get("zones", []):
                zones[f"{store_id}:{item['zone_id']}"] = ZoneConfig(
                    zone_id=item["zone_id"],
                    name=item.get("name", item["zone_id"]),
                    type=item.get("type", "zone"),
                    polygon=item.get("polygon", []),
                )
                zones.setdefault(
                    item["zone_id"],
                    ZoneConfig(
                        zone_id=item["zone_id"],
                        name=item.get("name", item["zone_id"]),
                        type=item.get("type", "zone"),
                        polygon=item.get("polygon", []),
                    ),
                )
        return StoreLayout(
            store_id=raw.get("default_store_id", raw["stores"][0]["store_id"]),
            store_name=raw.get("name", "Multi-store Layout"),
            timezone=raw.get("timezone", "Asia/Kolkata"),
            cameras=cameras,
            zones=zones,
            clip_start=default_clip_start,
            store_aliases=aliases,
        )

    layout_clip_start = parse_layout_clip_start(raw)
    return StoreLayout(
        store_id=raw["store_id"],
        store_name=raw.get("store_name", raw["store_id"]),
        timezone=raw.get("timezone", "UTC"),
        cameras=[
            CameraConfig(
                camera_id=item["camera_id"],
                filename=item["filename"],
                role=item.get("role", "UNKNOWN"),
                coverage_zones=item.get("coverage_zones", []),
                entry_threshold=item.get("entry_threshold"),
                clip_start=parse_layout_clip_start(item) or layout_clip_start,
                staff_hsv_ranges=item.get("staff_hsv_ranges", raw.get("staff_hsv_ranges", [])),
                entry_mode=item.get("entry_mode", "threshold"),
                entry_confirmation_frames=int(item.get("entry_confirmation_frames", 3)),
                entry_group=item.get("entry_group"),
            )
            for item in raw.get("cameras", [])
        ],
        zones={
            item["zone_id"]: ZoneConfig(
                zone_id=item["zone_id"],
                name=item.get("name", item["zone_id"]),
                type=item.get("type", "zone"),
                polygon=item.get("polygon", []),
            )
            for item in raw.get("zones", [])
        },
        clip_start=layout_clip_start,
        store_aliases={
            str(alias).casefold(): raw["store_id"]
            for alias in [raw["store_id"], raw.get("store_code"), *raw.get("aliases", [])]
            if alias
        },
    )
