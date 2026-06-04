import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.detector import create_detector
from pipeline.events import EventProcessor
from pipeline.layout import CameraConfig, StoreLayout, clip_start_from_filename, load_layout
from pipeline.pos import PosTransaction, load_pos_transactions
from pipeline.tracker import CentroidTracker, ReIdentifier


def write_bootstrap_events(output: Path, layout: StoreLayout) -> int:
    from pipeline.emit import build_event

    output.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    camera = layout.cameras[0] if layout.cameras else None
    store_id = camera.store_id if camera and camera.store_id else layout.store_id
    camera_id = camera.camera_id if camera else "BOOTSTRAP_CAMERA"
    zone_id = camera.coverage_zones[0] if camera and camera.coverage_zones else None
    events = [
        build_event(
            store_id=store_id,
            camera_id=camera_id,
            visitor_id="VIS_BOOTSTRAP",
            event_type="ENTRY",
            timestamp=now,
            confidence=0.99,
            session_seq=1,
        ),
        build_event(
            store_id=store_id,
            camera_id=camera_id,
            visitor_id="VIS_BOOTSTRAP",
            event_type="ZONE_ENTER",
            timestamp=now,
            zone_id=zone_id,
            confidence=0.92,
            sku_zone=layout.zone_name(store_id, zone_id),
            session_seq=2,
        ),
    ]
    with output.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    return len(events)


def iter_clips(clips_dir: Path, layout: StoreLayout) -> list[tuple[Path, CameraConfig]]:
    clips: list[tuple[Path, CameraConfig]] = []
    for clip_path in sorted(clips_dir.rglob("*.mp4")):
        camera = layout.camera_for_file(clip_path)
        if camera:
            clips.append((clip_path, camera))
    role_priority = {"ENTRY": 0, "MAIN_FLOOR": 1, "BILLING": 2, "BACK_AREA": 3}
    return sorted(
        clips,
        key=lambda item: (
            item[1].store_id or layout.store_id,
            role_priority.get(item[1].role, 9),
            item[0].name.lower(),
        ),
    )


def process_clip(
    *,
    clip_path: Path,
    camera: CameraConfig,
    layout: StoreLayout,
    output_handle,
    detector,
    frame_stride: int,
    max_frames: int | None,
    clip_start: datetime,
    pos_transactions: list[PosTransaction],
    shared_reidentifier: ReIdentifier,
) -> int:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open clip: {clip_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    tracker = CentroidTracker(max_distance=140, max_missed_frames=8)
    processor = EventProcessor(
        layout=layout,
        camera=camera,
        clip_start=clip_start,
        fps=fps,
        reidentifier=shared_reidentifier,
        pos_transactions=pos_transactions,
    )

    emitted = 0
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if max_frames is not None and frame_idx >= max_frames:
            break
        if frame_idx % frame_stride != 0:
            frame_idx += 1
            continue

        detections = detector.detect(frame, frame_idx)
        tracks = tracker.update(detections)
        for track in tracks:
            for event in processor.process_track(track, frame_idx):
                output_handle.write(json.dumps(event) + "\n")
                emitted += 1
        for track in tracker.last_expired_tracks:
            for event in processor.close_track(track, frame_idx):
                output_handle.write(json.dumps(event) + "\n")
                emitted += 1
        frame_idx += 1

    for track in list(tracker.tracks.values()):
        for event in processor.close_track(track, frame_idx):
            output_handle.write(json.dumps(event) + "\n")
            emitted += 1

    cap.release()
    return emitted


def parse_event_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonicalize_visitor_ids(
    events: list[dict],
    layout: StoreLayout,
    max_link_seconds: int = 120,
    min_entry_track_seconds: float = 4.0,
    entry_dedupe_seconds: float = 2.5,
) -> list[dict]:
    # 1. Run the remapping logic first
    from collections import defaultdict
    entry_times: dict[str, dict[str, datetime]] = {}
    first_seen: dict[tuple[str, str], datetime] = {}
    visitor_event_types: dict[tuple[str, str], set[str]] = {}
    local_times: dict[tuple[str, str], list[datetime]] = defaultdict(list)

    for event in events:
        store_id = event["store_id"]
        visitor_id = event["visitor_id"]
        timestamp = parse_event_time(event["timestamp"])
        key = (store_id, visitor_id)
        first_seen[key] = min(first_seen.get(key, timestamp), timestamp)
        local_times[key].append(timestamp)
        visitor_event_types.setdefault(key, set()).add(event["event_type"])

    valid_entry_keys = {
        key
        for key, timestamps in local_times.items()
        if "ENTRY" in visitor_event_types.get(key, set())
        and (max(timestamps) - min(timestamps)).total_seconds() >= min_entry_track_seconds
    }

    for event in events:
        store_id = event["store_id"]
        visitor_id = event["visitor_id"]
        timestamp = parse_event_time(event["timestamp"])
        key = (store_id, visitor_id)
        if event["event_type"] == "ENTRY" and not event.get("is_staff", False) and key in valid_entry_keys:
            store_entries = entry_times.setdefault(store_id, {})
            store_entries[visitor_id] = min(store_entries.get(visitor_id, timestamp), timestamp)

    remap: dict[tuple[str, str], str] = {}
    for key, seen_at in first_seen.items():
        store_id, visitor_id = key
        if key in valid_entry_keys:
            continue
        candidates = entry_times.get(store_id, {})
        if not candidates:
            continue
        nearest_id, nearest_time = min(
            candidates.items(),
            key=lambda item: abs((seen_at - item[1]).total_seconds()),
        )
        if abs((seen_at - nearest_time).total_seconds()) <= max_link_seconds:
            remap[key] = nearest_id

    canonical_events: list[dict] = []
    for event in events:
        updated = dict(event)
        key = (updated["store_id"], updated["visitor_id"])
        if updated["event_type"] == "ENTRY" and key not in valid_entry_keys:
            continue
        canonical_id = remap.get(key)
        if canonical_id and canonical_id != updated["visitor_id"]:
            metadata = dict(updated.get("metadata") or {})
            metadata.setdefault("original_visitor_id", updated["visitor_id"])
            metadata.setdefault("identity_link", "nearest_entry")
            updated["visitor_id"] = canonical_id
            updated["metadata"] = metadata
        canonical_events.append(updated)

    camera_groups = {
        camera.camera_id: camera.entry_group or camera.camera_id
        for camera in layout.cameras
        if camera.role == "ENTRY"
    }
    entry_clusters: dict[tuple[str, str], list[tuple[datetime, str]]] = defaultdict(list)
    dedupe_remap: dict[tuple[str, str], str] = {}
    for event in sorted(canonical_events, key=lambda item: parse_event_time(item["timestamp"])):
        if event["event_type"] != "ENTRY" or event.get("is_staff", False):
            continue
        key = (event["store_id"], camera_groups.get(event["camera_id"], event["camera_id"]))
        timestamp = parse_event_time(event["timestamp"])
        clusters = entry_clusters[key]
        if clusters and (timestamp - clusters[-1][0]).total_seconds() <= entry_dedupe_seconds:
            dedupe_remap[(event["store_id"], event["visitor_id"])] = clusters[-1][1]
        else:
            clusters.append((timestamp, event["visitor_id"]))

    for event in canonical_events:
        canonical_id = dedupe_remap.get((event["store_id"], event["visitor_id"]))
        if canonical_id and canonical_id != event["visitor_id"]:
            metadata = dict(event.get("metadata") or {})
            metadata.setdefault("original_visitor_id", event["visitor_id"])
            metadata.setdefault("identity_link", "entry_time_cluster")
            event["visitor_id"] = canonical_id
            event["metadata"] = metadata

    # 2. Behavior-based staff identification on canonicalized visitors
    visitor_events = defaultdict(list)
    for event in canonical_events:
        key = (event["store_id"], event["visitor_id"])
        visitor_events[key].append(event)

    staff_visitors = set()
    for key, evs in visitor_events.items():
        staff_count = sum(1 for e in evs if e.get("is_staff", False))
        ratio = staff_count / len(evs) if evs else 0.0
        
        store_id = key[0]
        visited_staff_zone = any(
            layout.zone_has_type(store_id, e.get("zone_id"), "back_area", "staff_or_storage")
            for e in evs
        )
        
        if ratio >= 0.25 or visited_staff_zone:
            staff_visitors.add(key)

    # Force consistent is_staff flag for all events of the canonical visitor
    for event in canonical_events:
        key = (event["store_id"], event["visitor_id"])
        event["is_staff"] = (key in staff_visitors)

    # 3. Filter out pass-by visitors / noise on the canonicalized events
    canonical_groups = defaultdict(list)
    for event in canonical_events:
        key = (event["store_id"], event["visitor_id"])
        canonical_groups[key].append(event)

    valid_keys = set()
    for key, evs in canonical_groups.items():
        # Staff are always kept
        if any(e.get("is_staff", False) for e in evs):
            valid_keys.add(key)
            continue

        has_confirmed_entry = any(e.get("event_type") == "ENTRY" for e in evs)
        if not has_confirmed_entry:
            continue

        visited_real_zone = any(
            e.get("zone_id") is not None
            and not layout.zone_has_type(
                key[0],
                e.get("zone_id"),
                "threshold",
                "floor",
                "back_area",
                "staff_or_storage",
                "billing",
            )
            for e in evs
        )
        visited_billing = any(e.get("event_type") == "BILLING_QUEUE_JOIN" for e in evs)
        if not visited_real_zone and not visited_billing:
            continue

        # Check total tracking duration
        timestamps = [parse_event_time(e["timestamp"]) for e in evs]
        if timestamps:
            duration = (max(timestamps) - min(timestamps)).total_seconds()
            if duration < 4.0:
                continue

        valid_keys.add(key)

    filtered_events = [
        event
        for event in canonical_events
        if (event["store_id"], event["visitor_id"]) in valid_keys
    ]
    return filtered_events


def infer_clip_start(
    *,
    clip_path: Path,
    camera: CameraConfig,
    layout: StoreLayout,
    pos_transactions: list[PosTransaction],
) -> datetime:
    if camera.clip_start:
        return camera.clip_start

    filename_start = clip_start_from_filename(clip_path)
    if filename_start:
        return filename_start

    if layout.clip_start:
        return layout.clip_start

    store_id = camera.store_id or layout.store_id
    canonical_store = layout.canonical_store_id(store_id)
    store_transactions = [
        transaction.timestamp
        for transaction in pos_transactions
        if layout.canonical_store_id(transaction.store_id) == canonical_store
    ]
    if store_transactions:
        earliest = min(store_transactions)
        return earliest.replace(hour=0, minute=0, second=0, microsecond=0)

    raise ValueError(
        f"Cannot infer clip start for {clip_path}. Configure clip_start/business_date, "
        "include a timestamp in the filename, or provide POS data for the same store."
    )


def process_all_clips(
    *,
    clips_dir: Path,
    layout_path: Path,
    output: Path,
    model_name: str,
    frame_stride: int,
    max_frames: int | None,
    prefer_yolo: bool,
    pos_path: Path,
    reid_threshold: float = 0.75,
    cross_camera_link_seconds: int = 120,
    min_entry_track_seconds: float = 4.0,
    entry_dedupe_seconds: float = 2.5,
) -> int:
    layout = load_layout(layout_path)
    pos_transactions = load_pos_transactions(pos_path)
    clips = iter_clips(clips_dir, layout)
    if not clips:
        raise RuntimeError(f"No matching .mp4 clips found in {clips_dir}")

    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    store_reidentifiers: dict[str, ReIdentifier] = {}
    raw_output = output.with_suffix(".raw.jsonl")
    with raw_output.open("w", encoding="utf-8") as handle:
        for clip_path, camera in clips:
            detector = create_detector(
                model_name,
                prefer_yolo=prefer_yolo,
                staff_hsv_ranges=camera.staff_hsv_ranges,
            )
            store_id = layout.canonical_store_id(camera.store_id or layout.store_id)
            store_reidentifier = store_reidentifiers.setdefault(
                store_id,
                ReIdentifier(threshold=reid_threshold),
            )
            clip_start = infer_clip_start(
                clip_path=clip_path,
                camera=camera,
                layout=layout,
                pos_transactions=pos_transactions,
            )
            emitted = process_clip(
                clip_path=clip_path,
                camera=camera,
                layout=layout,
                output_handle=handle,
                detector=detector,
                frame_stride=frame_stride,
                max_frames=max_frames,
                clip_start=clip_start,
                shared_reidentifier=store_reidentifier,
                pos_transactions=pos_transactions,
            )
            total += emitted
            print(f"{clip_path.name}: emitted {emitted} events using {detector.name}")
    events = [
        json.loads(line)
        for line in raw_output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = canonicalize_visitor_ids(
        events,
        layout,
        max_link_seconds=cross_camera_link_seconds,
        min_entry_track_seconds=min_entry_track_seconds,
        entry_dedupe_seconds=entry_dedupe_seconds,
    )
    final_output = output.with_suffix(".tmp.jsonl")
    with final_output.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    final_output.replace(output)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Retail CCTV detection pipeline starter.")
    parser.add_argument("--clips-dir", default="data/clips")
    parser.add_argument("--layout", default="data/store_layout.json")
    parser.add_argument("--output", default="output/events.jsonl")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--reid-threshold", type=float, default=0.75)
    parser.add_argument("--cross-camera-link-seconds", type=int, default=120)
    parser.add_argument("--min-entry-track-seconds", type=float, default=4.0)
    parser.add_argument("--entry-dedupe-seconds", type=float, default=2.5)
    parser.add_argument("--frame-stride", type=int, default=10)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--pos", default="data/pos_transactions_normalized.csv")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--no-yolo", action="store_true")
    args = parser.parse_args()

    if args.bootstrap:
        count = write_bootstrap_events(Path(args.output), load_layout(Path(args.layout)))
        print(f"Wrote {count} bootstrap events to {args.output}")
        return

    count = process_all_clips(
        clips_dir=Path(args.clips_dir),
        layout_path=Path(args.layout),
        output=Path(args.output),
        model_name=args.model,
        frame_stride=max(args.frame_stride, 1),
        max_frames=args.max_frames,
        prefer_yolo=not args.no_yolo,
        pos_path=Path(args.pos),
        reid_threshold=args.reid_threshold,
        cross_camera_link_seconds=max(args.cross_camera_link_seconds, 1),
        min_entry_track_seconds=max(args.min_entry_track_seconds, 0.0),
        entry_dedupe_seconds=max(args.entry_dedupe_seconds, 0.0),
    )
    print(f"Events written to {args.output}")
    print(f"Total events: {count}")


if __name__ == "__main__":
    main()
