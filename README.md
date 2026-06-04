# Purplle Store Intelligence

End-to-end retail analytics platform: CCTV detection pipeline → structured event stream → FastAPI backend → live React dashboard.

Works with **any number of stores** — drop in your data and run one command.

---

## Quick Start (Single Command)

```bash
git clone <repo-url>
cd purplle-store-intelligence
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| 📊 Dashboard | http://localhost:3000 |
| 🔌 API | http://localhost:8000 |
| 📖 API Docs (Swagger) | http://localhost:8000/docs |

The dashboard appears automatically after the pipeline finishes processing your video clips. No manual steps required.

---

## How It Works

```
docker compose up --build
        │
        ├─► db (PostgreSQL) + redis start and become healthy
        ├─► api starts → creates tables → seeds POS transactions from CSV
        ├─► pipeline runs:
        │     1. Auto-normalizes POS CSV if needed
        │     2. Reads data/store_layout.json (discovers all stores)
        │     3. Detects & tracks people in every video clip
        │     4. Runs cross-camera re-identification + staff filtering
        │     5. Emits structured events → output/events.jsonl
        │     6. POSTs all events to /events/replace on the API
        └─► dashboard starts at http://localhost:3000
```

---

## Project Structure

```
purplle-store-intelligence/
├── app/                         FastAPI backend
│   ├── main.py                  App entrypoint, middleware, routers
│   ├── db.py                    SQLAlchemy models, DB init, POS seeding
│   ├── models.py                Pydantic request/response schemas
│   ├── analytics.py             Pure analytics functions (no DB deps)
│   ├── replay.py                Live replay engine (snapshot endpoint)
│   ├── ingestion.py             /events/ingest and /events/replace
│   ├── metrics.py               /stores/{id}/metrics
│   ├── heatmap.py               /stores/{id}/heatmap
│   ├── funnel.py                /stores/{id}/funnel
│   ├── anomalies.py             /stores/{id}/anomalies
│   ├── health.py                /health
│   ├── stream.py                SSE stream endpoint
│   ├── layout_config.py         Zone type classification (heatmap filtering)
│   └── store_ids.py             Store ID canonicalization helper
│
├── pipeline/                    CCTV detection pipeline
│   ├── detect.py                Main orchestrator (process all clips)
│   ├── detector.py              YOLOv8 / OpenCV motion detector
│   ├── tracker.py               Centroid tracker + Re-Identifier
│   ├── events.py                EventProcessor (zone dwell, billing, entry)
│   ├── layout.py                Layout JSON parser + camera config
│   ├── pos.py                   POS transaction loader
│   ├── emit.py                  Event schema builder
│   └── staff.py                 Staff detection helpers
│
├── scripts/
│   ├── run_pipeline.py          Docker entrypoint (normalize → detect → ingest)
│   ├── normalize_data.py        POS CSV normalizer (IST→UTC, group by order)
│   └── ingest_events.py        Manual ingest helper (standalone)
│
├── dashboard/
│   ├── src/App.jsx              React dashboard (live replay + metrics)
│   └── src/styles.css          Dashboard styles
│
├── tests/                       31 pytest tests
├── data/
│   ├── store_layout.json        Store/camera/zone definitions (source of truth)
│   ├── POS*.csv                 Raw POS transactions (drop yours here)
│   ├── pos_transactions_normalized.csv  (auto-generated)
│   ├── store1/                  Store 1 video clips + layout PNG
│   └── store2/                  Store 2 video clips + layout PNG
│
├── output/
│   └── events.jsonl             Pipeline output (auto-generated)
│
├── Dockerfile.api
├── Dockerfile.pipeline
├── docker-compose.yml
├── .env.example
├── run_pipeline.py              Diagnostic: per-store event summary
├── check_events.py              Diagnostic: per-visitor event breakdown
└── yolov8n.pt                   YOLOv8 nano weights
```

---

## Adding a New Store (Zero Code Changes)

### Step 1 — Define the store in `data/store_layout.json`

```json
{
  "stores": [
    {
      "store_id": "ST9999",
      "store_name": "My New Store",
      "city": "Mumbai",
      "source_dir": "my_store",
      "layout_image": "my_store/layout.png",
      "clip_start": "2026-06-01T10:00:00Z",
      "floor_plan": { "image_width": 1920, "image_height": 1080 },
      "cameras": [
        {
          "camera_id": "ST9999_CAM_ENTRY",
          "filename": "entry.mp4",
          "role": "ENTRY",
          "coverage_zones": ["ENTRY"],
          "entry_threshold": {
            "line": [[960, 0], [960, 1080]],
            "inbound_direction": "right"
          }
        },
        {
          "camera_id": "ST9999_CAM_FLOOR",
          "filename": "floor.mp4",
          "role": "MAIN_FLOOR",
          "coverage_zones": ["FOH", "ZONE_A", "ZONE_B"]
        },
        {
          "camera_id": "ST9999_CAM_BILLING",
          "filename": "billing.mp4",
          "role": "BILLING",
          "coverage_zones": ["CASH_COUNTER"]
        }
      ],
      "zones": [
        { "zone_id": "ENTRY",        "name": "Entry",        "type": "threshold", "polygon": [[0,900],[200,900],[200,1080],[0,1080]] },
        { "zone_id": "FOH",          "name": "Floor",        "type": "floor",     "polygon": [[200,0],[1700,0],[1700,900],[200,900]] },
        { "zone_id": "ZONE_A",       "name": "Zone A",       "type": "sku_zone",  "polygon": [[300,100],[600,100],[600,400],[300,400]] },
        { "zone_id": "ZONE_B",       "name": "Zone B",       "type": "sku_zone",  "polygon": [[700,100],[1000,100],[1000,400],[700,400]] },
        { "zone_id": "CASH_COUNTER", "name": "Cash Counter", "type": "billing",   "polygon": [[1500,200],[1700,200],[1700,700],[1500,700]] }
      ]
    }
  ]
}
```

### Step 2 — Drop video clips

```
data/
  my_store/
    entry.mp4
    floor.mp4
    billing.mp4
    layout.png
```

### Step 3 — Add POS transactions

Add rows to `data/POS*.csv`:
```
store_id,order_date,order_time,total_amount
ST9999,01-06-2026,10:15:05,1299.00
ST9999,01-06-2026,11:42:30,450.50
```
> **Date:** DD-MM-YYYY · **Time:** HH:MM:SS (IST, 24-hour) · **store_id** must match `store_layout.json`

If store IDs don't match, use the rename flag:
```bash
python scripts/normalize_data.py --store-id-map OLD_ID=ST9999
```

### Step 4 — Run

```bash
docker compose up --build
```

---

## Zone Types Reference

| Type | Description | Shown in heatmap? |
|---|---|---|
| `threshold` | Entry/exit doorway | No |
| `floor` | General floor area (FOH) | No |
| `sku_zone` | Product shelf or brand zone | Yes |
| `shelf` | Physical shelf unit | Yes |
| `service` | Service counter (makeup, nails, etc.) | Yes |
| `billing` | Cash counter / checkout queue area | No |
| `back_area` | Staff-only storage or back of house | No |

---

## Pipeline Tuning

Override any of these in `.env` without touching code:

| Variable | Default | Description |
|---|---|---|
| `FRAME_STRIDE` | `15` | Frames skipped between detections (lower = more accurate) |
| `REID_THRESHOLD` | `0.75` | Re-ID histogram similarity threshold (higher = stricter) |
| `CROSS_CAMERA_LINK_SECONDS` | `120` | Max gap (s) to link the same person across cameras |
| `MIN_ENTRY_TRACK_SECONDS` | `4` | Minimum tracked duration (s) to count a visitor |
| `ENTRY_DEDUPE_SECONDS` | `2.5` | Dedup window (s) for multi-camera entry counting |
| `PIPELINE_NO_YOLO` | `0` | Set `1` to use OpenCV motion fallback (no GPU needed) |
| `POS_STORE_ID_MAP` | _(empty)_ | Rename store IDs e.g. `OUTLET_A=ST1008,OUTLET_B=ST1076` |

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health + DB/Redis connectivity |
| `GET` | `/replay/snapshot?speed=N` | Live replay snapshot with metrics for all stores |
| `GET` | `/stores/{id}/metrics` | Aggregated store metrics |
| `GET` | `/stores/{id}/heatmap` | Zone-level heatmap (visit count + dwell + heat score) |
| `GET` | `/stores/{id}/funnel` | Customer funnel: Entry → Zone Visit → Billing → Purchase |
| `GET` | `/stores/{id}/anomalies` | Detected anomalies (queue spike, conversion drop, dead zone) |
| `POST` | `/events/ingest` | Append new events (partial success — per-event rejection reasons) |
| `POST` | `/events/replace` | Replace all events for a set of stores atomically |
| `GET` | `/docs` | Swagger UI |

---

## Key Metrics Explained

| Metric | How It's Computed |
|---|---|
| **Unique visitors** | Distinct `visitor_id` values from `ENTRY` events where `is_staff=false` |
| **Conversion rate** | Visitors with a `BILLING_QUEUE_JOIN` followed by a POS transaction within 5 minutes ÷ unique visitors |
| **Queue depth** | `queue_depth` field from the most recent `BILLING_QUEUE_JOIN` event |
| **Abandonment rate** | `BILLING_QUEUE_ABANDON` count ÷ `BILLING_QUEUE_JOIN` count |
| **Avg dwell per zone** | Mean `dwell_ms` from `ZONE_DWELL` events, grouped by `zone_id` |
| **Heatmap heat score** | Zone visit count ÷ max zone visit count × 100 (normalized 0–100) |

---

## Diagnostics (Local)

```bash
# Summary: per-store visitor + event type counts
python run_pipeline.py

# Detail: per-visitor event breakdown for every store
python check_events.py

# Normalize POS CSV manually (with optional store ID rename)
python scripts/normalize_data.py
python scripts/normalize_data.py --store-id-map OUTLET_A=ST1008
```

---

## Tests

```bash
python run_tests.py
# 31 tests · anomalies, API endpoints, funnel, ingestion, metrics, pipeline
```
