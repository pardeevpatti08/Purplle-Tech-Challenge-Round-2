import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


def chunks(items: list[dict], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def post_batch(url: str, events: list[dict]) -> dict:
    body = json.dumps({"events": events}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest JSONL pipeline events into the API.")
    parser.add_argument("jsonl", nargs="?", default="output/events.jsonl")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    events = [json.loads(line) for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines() if line.strip()]
    accepted = 0
    rejected = 0
    for batch in chunks(events, min(args.batch_size, 500)):
        result = post_batch(f"{args.api.rstrip('/')}/events/ingest", batch)
        accepted += int(result.get("accepted", 0))
        rejected += len(result.get("rejected", []))
        print(result)
    print(f"Ingested {accepted} new events; rejected {rejected}.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        raise SystemExit(f"API unavailable: {exc}") from exc
