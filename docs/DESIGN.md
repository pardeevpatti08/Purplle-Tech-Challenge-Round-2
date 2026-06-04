# Design

## Architecture Overview

The system converts raw CCTV footage into queryable retail behavioral events through four loosely coupled components: a detection pipeline, a REST API, a PostgreSQL event store, and a React dashboard. The components communicate only through well-defined contracts (a JSONL event format and HTTP JSON endpoints), making each layer replaceable independently.

```
CCTV clips (MP4)
  → pipeline/detect.py            person detection + tracking
  → canonicalize_visitor_ids()    cross-camera re-ID + staff filtering
  → output/events.jsonl           structured JSONL event stream
  → POST /events/replace          atomic ingest into PostgreSQL
  → /replay/snapshot              live time-windowed metrics
  → GET /stores/{id}/metrics      aggregated analytics
  → dashboard (React/Vite)        live visualization
```

---

## Component Design

### 1. Detection Pipeline (`pipeline/`)

The pipeline reads `data/store_layout.json` to discover all stores and the camera clips that belong to each. It processes clips in role order — ENTRY cameras first, then MAIN_FLOOR, then BILLING — so that entry events are available when floor cameras need to link tracks.

**Per-clip flow:**
1. `CentroidTracker` assigns local track IDs to bounding boxes in each frame.
2. `ReIdentifier` links tracks across frames using HSV histogram similarity (`--reid-threshold`, default 0.75). A separate `ReIdentifier` instance is maintained per store so cross-store IDs never collide.
3. `EventProcessor` translates track state changes into typed events: `ENTRY`, `ZONE_ENTER`, `ZONE_DWELL`, `ZONE_EXIT`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`.
4. Zone assignment uses a point-in-polygon test against the store's layout polygons.
5. The billing queue join/abandon state machine tracks how long a person is in the billing zone and emits `BILLING_QUEUE_JOIN` with a `queue_depth` metadata field.

**Post-clip canonicalization (`canonicalize_visitor_ids`):**

After all clips are processed, a three-pass canonicalization step runs over the raw event stream:
- **Pass 1 — Short track filtering:** Tracks shorter than `min_entry_track_seconds` (default 4 s) that don't have a valid entry event are dropped. This removes pass-by detections of people who never entered.
- **Pass 2 — Cross-camera ID merging:** Tracks that appear within `cross_camera_link_seconds` (default 120 s) of a known entry visitor are merged to that visitor's canonical ID. The original track ID is preserved in `metadata.original_visitor_id`.
- **Pass 3 — Entry deduplication:** Multiple entry cameras covering the same physical door (grouped by `entry_group` in the layout) can fire within `entry_dedupe_seconds` (default 2.5 s). The second fire is merged into the first to prevent double-counting.
- **Staff identification:** After ID merging, any visitor where ≥25% of their events carry `is_staff=true` (from HSV colour-range matching in the detector), or who visits a `back_area` / `staff_or_storage` zone, is flagged as staff across all their events. Staff are excluded from all customer-facing metrics.

**Clip start inference:**  
The pipeline needs to know the real-world timestamp of the first video frame to convert frame numbers to UTC timestamps. It resolves this in priority order: camera-level `clip_start` → filename timestamp pattern → layout-level `clip_start` → earliest POS transaction date for the same store. This makes the system robust to both timestamped filenames and explicit configuration.

---

### 2. Event Schema

Each event is a flat JSON object with the following top-level fields:

| Field | Type | Description |
|---|---|---|
| `event_id` | UUID string | Unique event identifier |
| `store_id` | string | Store the event belongs to |
| `camera_id` | string | Camera that recorded it |
| `visitor_id` | string | Canonical visitor/track identifier |
| `event_type` | enum | One of: `ENTRY`, `REENTRY`, `ZONE_ENTER`, `ZONE_DWELL`, `ZONE_EXIT`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON` |
| `timestamp` | ISO-8601 UTC | When the event occurred |
| `zone_id` | string \| null | Zone involved (null for ENTRY events) |
| `dwell_ms` | integer | Time spent in zone (ms); 0 for non-dwell events |
| `is_staff` | boolean | True if visitor was identified as a store employee |
| `confidence` | float [0–1] | Detector confidence for the underlying detection |
| `metadata` | JSON object | Event-specific extra fields (see below) |

**Metadata fields by event type:**

| Event type | Metadata fields |
|---|---|
| `ENTRY` | `session_seq`, `original_visitor_id` (if re-IDed) |
| `ZONE_ENTER` / `ZONE_DWELL` | `sku_zone` (zone name), `dwell_seconds` |
| `BILLING_QUEUE_JOIN` | `queue_depth` (integer, current queue length) |
| `BILLING_QUEUE_ABANDON` | `wait_seconds` (time in queue before leaving) |

---

### 3. API (`app/`)

FastAPI with async SQLAlchemy (PostgreSQL) and Redis. All event data flows through two paths:

**Replay path (primary for dashboard):**  
`GET /replay/snapshot` reads `output/events.jsonl` directly from disk (not the database) using `lru_cache` keyed on file mtime and size. It simulates a live time window by cycling a cursor through the event timeline at `speed`× real time. All analytics are computed in-memory on the filtered event slice. This makes the dashboard feel live without polling the database on every tick.

**Database path (persistent store):**  
`POST /events/replace` atomically deletes existing events for the affected store IDs and bulk-inserts the new set. `GET /stores/{id}/metrics`, `/heatmap`, `/funnel`, and `/anomalies` all query the database via async SQLAlchemy. POS transactions are seeded from `data/pos_transactions_normalized.csv` at startup via `init_db()`.

**Conversion rate calculation:**  
A visitor is counted as converted if they have at least one `BILLING_QUEUE_JOIN` event and there is a POS transaction for the same store within a 5-minute window after that event. This correlates CCTV billing queue data with actual POS receipts.

**Heatmap filtering:**  
Zone types `threshold`, `floor`, `billing`, `back_area`, and `staff_or_storage` are excluded from the heatmap. Only product-facing zones (`sku_zone`, `shelf`, `service`) appear in the heatmap, preventing structural zones like ENTRY and FOH from dominating the heat score.

**Anomaly detection:**  
Three anomaly types are computed on demand:
- `BILLING_QUEUE_SPIKE` — queue depth >5 (WARN) or >10 (CRITICAL)
- `CONVERSION_DROP` — today's conversion rate is more than 20% below the 7-day rolling average
- `DEAD_ZONE` — a product zone has had no `ZONE_ENTER` events in the past 30 minutes

---

### 4. Layout Configuration (`data/store_layout.json`)

The layout JSON is the single source of truth for the entire system. It defines:
- Which stores exist and their display names
- Which video files belong to each camera
- Camera roles (`ENTRY`, `MAIN_FLOOR`, `BILLING`, `BACK_AREA`) that control event processing logic
- Zone polygons in camera pixel coordinates for point-in-polygon zone assignment
- Zone types that control heatmap inclusion, staff zone detection, and billing state machine activation
- Optional `clip_start` timestamps, `staff_hsv_ranges` for colour-based staff detection, and `entry_group` for multi-camera door deduplication

No store IDs, camera IDs, or zone names are hardcoded anywhere in the application or pipeline code. Adding a new store requires only editing this file, dropping clips, and running `docker compose up --build`.

---

### 5. Dashboard (`dashboard/`)

React + Vite single-page application. It polls `GET /replay/snapshot?speed=<N>` every second and renders:
- Per-store live metrics cards (visitors, conversion rate, queue depth, abandonment)
- Customer funnel chart (Entry → Zone Visit → Billing → Purchase)
- Zone heatmap overlaid on the store layout PNG
- Live replay progress bar showing elapsed position in the event timeline

The dashboard is served from its own Docker container and communicates with the API via `VITE_API_URL` (configurable in docker-compose.yml).

---

## Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                        Docker Compose                        │
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌────────────────────────┐ │
│  │  db      │    │  redis   │    │  pipeline              │ │
│  │(postgres)│    │          │    │  1. normalize POS CSV  │ │
│  └────┬─────┘    └────┬─────┘    │  2. detect.py          │ │
│       │healthy        │healthy   │  3. canonicalize IDs   │ │
│       │               │          │  4. write events.jsonl │ │
│       ▼               ▼          │  5. POST /events/replace│ │
│  ┌────────────────────────────┐  └────────────┬───────────┘ │
│  │          api               │               │             │
│  │  - init_db() on start      │◄──────────────┘             │
│  │  - seed POS transactions   │                             │
│  │  - /replay/snapshot        │  ┌─────────────────────┐   │
│  │  - /stores/{id}/metrics    │◄─│     dashboard        │   │
│  │  - /stores/{id}/heatmap    │  │  polls /snapshot     │   │
│  │  - /stores/{id}/anomalies  │  │  every 1 second      │   │
│  └────────────────────────────┘  └─────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

---

## Edge Cases and How They Are Handled

| Scenario | Handling |
|---|---|
| Person walks past the door without entering | Filtered by `MIN_ENTRY_TRACK_SECONDS` (4 s min tracking duration) |
| Two entry cameras covering the same door | Deduplicated by `entry_dedupe_seconds` window within the same `entry_group` |
| Same person seen on multiple cameras | Cross-camera re-ID links tracks using HSV histogram similarity within `cross_camera_link_seconds` |
| Staff moving through store | HSV colour-range detection + back_area zone visit flag; all events for that ID marked `is_staff=true` |
| POS timestamp doesn't exactly match queue join | 5-minute forward window applied: any POS transaction within 5 min of queue join counts as a conversion |
| Partial occlusion of a person | Low-confidence events are retained (not suppressed); `confidence` field allows filtering downstream |
| Empty store periods | All metrics return 0 / empty collections — no nulls or errors |
| Missing POS CSV | POS seeding is skipped silently; conversion rate returns 0.0 |
| New store added with mismatched store IDs | `--store-id-map` flag in `normalize_data.py` / `POS_STORE_ID_MAP` env var handle renaming without code changes |

---

## Design Decisions

**1. Scaffold depth**  
A narrow but runnable base was built before expanding toward a full production scaffold. Having a working API and Docker setup early allowed the event schema, endpoint contracts, and PostgreSQL connectivity to be validated before video processing was added.

**2. Partial ingestion vs. all-or-nothing**  
`POST /events/ingest` allows partial success and returns per-event rejection reasons. This matches the expected API contract and prevents a single malformed event from blocking an entire batch.

**3. SQLite vs. PostgreSQL from day one**  
PostgreSQL was used from the beginning because the system is designed to be production-aware and Dockerized. The small initial setup cost avoids a later storage migration, while async SQLAlchemy provides a consistent development experience.

**4. Re-identification approach**  
HSV histogram similarity was chosen over deep-learning Re-ID models such as OSNet and FastReID. It runs on CPU without additional model downloads and is suitable for retail environments with relatively stable lighting. A deep-learning model can later be introduced by replacing the `ReIdentifier` class without changing event logic.

**5. Conversion rate correlation strategy**  
CCTV visitor IDs are anonymous, and POS transactions have no direct camera link. The system therefore uses time-window correlation: a visitor who reaches the billing queue within five minutes before a POS transaction is counted as converted. This avoids the need for any PII linkage.

**6. Hardcoded store IDs vs. data-driven layout**  
Early versions hardcoded `ST1008` and `ST1076` throughout the pipeline and normalization scripts. The design was generalized by making `store_layout.json` the single source of truth. The pipeline discovers stores from the layout, `normalize_data.py` uses the store IDs present in the raw POS CSV, and `--store-id-map` handles cases where POS and layout IDs differ. Adding a new store requires no code changes.

**7. Heatmap zone filtering**  
Early heatmap output was dominated by structural zones such as ENTRY, FOH, and CASH_COUNTER. Zones are now classified by `type` in the layout JSON, and the types `threshold`, `floor`, `billing`, `back_area`, and `staff_or_storage` are excluded from the heatmap. This keeps filtering data-driven instead of relying on hardcoded zone ID strings.

**8. Live dashboard replay strategy**  
A replay engine was chosen instead of direct database polling. It reads `events.jsonl` from disk and simulates a moving time cursor. This avoids querying the database on every dashboard poll, uses `lru_cache` keyed on file modification time for low overhead, and creates a natural live-store experience by cycling through historical events at a configurable speed.
