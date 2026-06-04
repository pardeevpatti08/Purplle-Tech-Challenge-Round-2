"""
check_events.py
---------------
Diagnostic: prints a detailed per-visitor breakdown for every store found
in output/events.jsonl. Works with any store IDs — nothing is hardcoded.

Usage:
    python check_events.py [path/to/events.jsonl]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_PATH = Path("output/events.jsonl")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH

    if not path.exists():
        print(f"Events file not found: {path}")
        sys.exit(1)

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    by_store: dict[str, list] = defaultdict(list)
    for e in events:
        by_store[e["store_id"]].append(e)

    for store_id in sorted(by_store):
        store_events = by_store[store_id]
        print(f"\n{'='*70}")
        print(f"Store: {store_id}  ({len(store_events)} total events)")
        print(f"{'='*70}")

        visitor_map: dict[str, list] = defaultdict(list)
        for e in store_events:
            visitor_map[e["visitor_id"]].append(e)

        print(f"Unique visitor IDs: {len(visitor_map)}")
        print(f"\nPer-visitor detail (first 20):")
        for vid, evs in list(visitor_map.items())[:20]:
            cams = sorted({e["camera_id"] for e in evs})
            zones = sorted({e["zone_id"] for e in evs if e.get("zone_id")})
            types = sorted({e["event_type"] for e in evs})
            times = sorted(e["timestamp"] for e in evs)
            is_staff = any(e.get("is_staff") for e in evs)
            print(
                f"  {vid} | staff={is_staff} | events={len(evs)} "
                f"| types={types} | zones={zones} "
                f"| {times[0]} → {times[-1]}"
            )


if __name__ == "__main__":
    main()
