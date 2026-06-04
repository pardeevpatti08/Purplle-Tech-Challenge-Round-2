"""
run_pipeline.py
---------------
Quick diagnostic: reads output/events.jsonl and prints a per-store summary
of event counts, unique visitor counts, and event type breakdown.

Works with any number of stores - no store IDs are hardcoded.

Usage:
    python run_pipeline.py [path/to/events.jsonl]
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
        print("Run the pipeline first (docker compose up --build) to generate events.")
        sys.exit(1)

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Total events loaded: {len(events)}")

    # Group by store
    by_store: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_store[e["store_id"]].append(e)

    print(f"Stores found: {sorted(by_store)}\n")

    for store_id in sorted(by_store):
        store_events = by_store[store_id]
        unique_visitors = {e["visitor_id"] for e in store_events if not e.get("is_staff")}
        staff_ids = {e["visitor_id"] for e in store_events if e.get("is_staff")}

        event_types: dict[str, int] = defaultdict(int)
        for e in store_events:
            event_types[e["event_type"]] += 1

        print(f"{'='*60}")
        print(f"  Store: {store_id}")
        print(f"  Total events       : {len(store_events)}")
        print(f"  Unique customers   : {len(unique_visitors)}")
        print(f"  Staff IDs detected : {len(staff_ids)}")
        print(f"  Event type counts  :")
        for etype, count in sorted(event_types.items()):
            print(f"    {etype:<30} {count}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
