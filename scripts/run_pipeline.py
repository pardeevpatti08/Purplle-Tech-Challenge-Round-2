"""
scripts/run_pipeline.py
-----------------------
Docker entrypoint for the pipeline service.

Sequence:
  1. Auto-normalize POS data (if pos_transactions_normalized.csv is missing or stale)
  2. Run the CCTV detection pipeline (detect.py) on all stores in store_layout.json
  3. Wait for the API to be ready
  4. POST all events to /events/replace on the API

All paths and tuning parameters are driven by environment variables set in
docker-compose.yml / .env — no store IDs or file paths are hardcoded here.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def wait_for_api(api_url: str, timeout_seconds: int = 120) -> None:
    print(f"Waiting for API at {api_url} ...")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{api_url}/health", timeout=5):
                print("API is ready.")
                return
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"API did not become ready within {timeout_seconds}s")


def normalize_pos_if_needed(pos_path: Path, data_dir: Path) -> None:
    """Run normalize_data.py if the normalized CSV doesn't exist yet."""
    if pos_path.exists():
        return
    raw_csvs = sorted(data_dir.glob("POS*.csv"))
    if not raw_csvs:
        print(
            "WARNING: No POS*.csv found in data/. "
            "Conversion rate will be 0 for all stores."
        )
        return
    print(f"Normalizing POS data from {raw_csvs[0].name} ...")
    # normalize_data.py lives alongside this file (scripts/)
    normalize_script = Path(__file__).parent / "normalize_data.py"
    store_id_map = os.getenv("POS_STORE_ID_MAP", "").strip()
    cmd = [sys.executable, str(normalize_script)]
    if store_id_map:
        cmd += ["--store-id-map"] + store_id_map.split(",")
    subprocess.run(cmd, check=True)


def replace_events(api_url: str, output: Path) -> None:
    print(f"Ingesting {output} into API ...")
    events = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    body = json.dumps({"events": events}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url}/events/replace",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("rejected"):
        raise RuntimeError(f"API rejected {len(result['rejected'])} events")
    print(f"Successfully ingested {result.get('accepted', 0)} events.")


def main() -> None:
    data_dir = Path(os.getenv("CLIPS_DIR", "data"))
    pos_path = Path(os.getenv("POS_PATH", "data/pos_transactions_normalized.csv"))
    output = Path(os.getenv("PIPELINE_OUTPUT", "output/events.jsonl"))
    api_url = os.getenv("API_URL", "http://api:8000").rstrip("/")

    # Step 1 — ensure POS CSV is normalized
    normalize_pos_if_needed(pos_path, data_dir)

    # Step 2 — run the detection pipeline
    detect_cmd = [
        sys.executable,
        "pipeline/detect.py",
        "--clips-dir", str(data_dir),
        "--layout",   os.getenv("LAYOUT_PATH", "data/store_layout.json"),
        "--pos",      str(pos_path),
        "--output",   str(output),
        "--model",    os.getenv("MODEL_PATH", "yolov8n.pt"),
        "--frame-stride",              os.getenv("FRAME_STRIDE", "15"),
        "--reid-threshold",            os.getenv("REID_THRESHOLD", "0.75"),
        "--cross-camera-link-seconds", os.getenv("CROSS_CAMERA_LINK_SECONDS", "120"),
        "--min-entry-track-seconds",   os.getenv("MIN_ENTRY_TRACK_SECONDS", "4"),
        "--entry-dedupe-seconds",      os.getenv("ENTRY_DEDUPE_SECONDS", "2.5"),
    ]
    if os.getenv("PIPELINE_NO_YOLO", "0") == "1":
        detect_cmd.append("--no-yolo")

    print("Starting detection pipeline ...")
    subprocess.run(detect_cmd, check=True)

    # Step 3 — wait for API and ingest
    wait_for_api(api_url)
    replace_events(api_url, output)
    print("Pipeline complete.")


if __name__ == "__main__":
    main()
