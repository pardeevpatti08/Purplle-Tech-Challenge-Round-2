# Choices

Key technical decisions made during development, covering model selection, schema design, and API architecture.

---

## Decision 1: Detection Model — YOLOv8n + OpenCV Fallback

**Options considered:** YOLOv8, YOLOv9, RT-DETR, MediaPipe Pose

**Choice:** YOLOv8n (nano) as the primary detector, with an OpenCV background-subtraction motion detector as an automatic fallback when ultralytics is not available.

**Rationale:**
- YOLOv8n is well-documented, widely used in retail CV projects, runs on CPU without a GPU, and produces reliable person detections at 640×640 in real time.
- The `--no-yolo` flag and automatic fallback mean the event schema, zone logic, and state machines can be developed and tested without waiting for a GPU environment.
- The detector is isolated behind a `create_detector()` factory function and a simple `detect(frame, frame_idx) → list[BoundingBox]` interface, making it straightforward to swap in YOLOv8l, YOLOv9, or RT-DETR for higher accuracy without touching event logic.

**Trade-offs accepted:**
- RT-DETR performs better on heavy occlusion and small objects, but it requires more compute and a larger model download.
- YOLOv8n has lower mAP than larger YOLO variants, but for retail settings with relatively large person silhouettes and stable camera angles it is sufficient.
- The OpenCV fallback produces noisier bounding boxes (background subtraction misses slow-moving people and creates ghost detections on camera jitter), so the `MIN_ENTRY_TRACK_SECONDS` filter is important when running without YOLO.

---

## Decision 2: Re-Identification Strategy — HSV Histogram Similarity

**Options considered:** Deep-learning Re-ID (OSNet, FastReID, torchreid), appearance embedding + FAISS index, HSV colour histogram cosine similarity

**Choice:** HSV histogram similarity with a configurable threshold (`--reid-threshold`, default 0.75).

**Rationale:**
- Retail store lighting is relatively stable, so colour histograms of person clothing are a reliable appearance descriptor between camera views.
- This approach requires no additional model download, no GPU, and runs in microseconds per track pair.
- The `ReIdentifier` class compares the most recent histogram of a disappearing track against all active tracks. If similarity exceeds the threshold and the time gap is within `--cross-camera-link-seconds`, the tracks are merged under the earlier visitor ID.
- The threshold and time window are fully configurable via environment variables so operators can tune accuracy vs. precision without code changes.

**Trade-offs accepted:**
- Two people wearing similar clothing may be incorrectly merged. In practice, this is rare in retail where customers are not wearing uniforms.
- Deep-learning Re-ID (e.g. OSNet) would perform better in environments with variable lighting or crowds, at the cost of ~50 MB model size and GPU dependency.

---

## Decision 3: Staff Exclusion — Colour-Range + Behaviour-Based

**Options considered:** Manual staff ID list, separate camera for staff entrance, HSV colour-range matching on known uniform colours, behaviour-based heuristics

**Choice:** Two-layer approach — HSV colour-range matching at the detector level, with a behaviour-based override at the canonicalization level.

**Rationale:**
- HSV colour-range matching (configurable `staff_hsv_ranges` per camera in the layout JSON) flags individual detections where the person's dominant colour falls within the configured range. This catches staff in uniform at the frame level.
- After cross-camera merging, the canonicalization step applies a second pass: any visitor where ≥25% of their events carry `is_staff=true`, or who visits a `back_area` / `staff_or_storage` zone, is permanently marked as staff across all their events. This prevents a single frame misclassification from marking a customer as staff, and catches staff who are not always wearing full uniform.
- The `is_staff` flag is propagated consistently across all events for a canonical visitor ID, so staff are excluded from unique visitor counts, conversion rate, heatmap, funnel, and anomaly metrics.

**Trade-offs accepted:**
- The HSV range must be configured manually per store. An unconfigured store will not filter staff by colour.
- A customer wearing colours similar to the staff uniform may be incorrectly excluded. The behaviour-based check (back_area visits) partly mitigates this.

---

## Decision 4: Event Schema — Challenge Schema with JSON Metadata

**Options considered:** Fully flat schema, deeply nested session schema, challenge-provided event schema with a `metadata` JSON blob

**Choice:** Challenge-provided schema with a `metadata` JSON object for event-specific fields.

**Rationale:**
- The fixed top-level fields (`event_id`, `store_id`, `camera_id`, `visitor_id`, `event_type`, `timestamp`, `zone_id`, `dwell_ms`, `is_staff`, `confidence`) are indexed in PostgreSQL and used for all filters and aggregations. This gives fast query performance without needing to parse JSON on every query.
- Event-specific data (`queue_depth`, `sku_zone`, `original_visitor_id`, `identity_link`, `wait_seconds`) lives in the `metadata` JSON blob. This keeps the schema stable as new event types are added.
- The schema is validated on ingestion using Pydantic (`EventIn` model). Invalid events are rejected with per-event reasons rather than blocking the whole batch.

**Trade-offs accepted:**
- Querying metadata fields requires JSON path operators in SQL and cannot use regular column indexes. Since no current endpoint filters or aggregates on metadata fields, this is acceptable.

---

## Decision 5: API Architecture — FastAPI + PostgreSQL + Redis

**Options considered:** FastAPI + SQLite, FastAPI + PostgreSQL, Node.js + PostgreSQL, queue-first (Kafka/Redis Streams)

**Choice:** FastAPI + PostgreSQL for the core, Redis reserved for live queue state and SSE streaming.

**Rationale:**
- FastAPI gives strong Pydantic request validation, async support via asyncio, and auto-generated Swagger docs at `/docs`.
- PostgreSQL handles the relational query patterns well: distinct visitor sessions, time-window aggregations, POS correlation by store and timestamp.
- Redis is used to hold live queue state so the SSE stream endpoint can push updates to the dashboard without querying the database on every client poll.
- Docker Compose starts all four services (`db`, `redis`, `api`, `pipeline`) in the correct dependency order with health checks. This means `docker compose up --build` is the only command a user needs to run.

**Trade-offs accepted:**
- SQLite would have been faster to set up but would require a migration before any production deployment. Starting with PostgreSQL eliminates that future risk.
- A queue-first architecture (Kafka, Redis Streams) would support higher event throughput but adds operational complexity. For a batch-then-serve workflow (pipeline runs once, API serves results), a simple REST ingest endpoint is sufficient.

---

## Decision 6: Live Dashboard — Replay Engine over Direct DB Polling

**Options considered:** Database polling on every request, Redis pub/sub, SSE with DB queries, file-based replay with `lru_cache`

**Choice:** File-based replay engine reading `output/events.jsonl` with `lru_cache` keyed on file modification time and size.

**Rationale:**
- The pipeline produces a static `events.jsonl` file after processing. Reading that file directly avoids a database round-trip on every dashboard poll.
- `lru_cache(maxsize=2)` keyed on `(path, mtime_ns, file_size)` means the file is parsed at most once per write. All subsequent polls within the same file version are served from memory.
- A time cursor advances through the event timeline at configurable speed (default 1×, max 20×), creating a "live store" experience from historical data.
- All analytics (unique visitors, conversion, queue depth, heatmap, funnel) are computed in-memory on the filtered event slice — no SQL needed for the dashboard path.

**Trade-offs accepted:**
- If `events.jsonl` is very large (millions of events), loading it entirely into memory may become slow. For the current dataset (~2,000 events) this is not an issue.
- The replay simulation cycles through historical events rather than updating from a live CCTV feed. A production deployment would replace the file-based replay with a real-time event stream.

---

## Decision 7: Conversion Rate Correlation — 5-Minute POS Window

**Options considered:** Exact timestamp match, fixed time window (1 min, 5 min, 10 min), session-based matching, visitor-level match ignoring time

**Choice:** 5-minute forward window from the visitor's `BILLING_QUEUE_JOIN` event.

**Rationale:**
- CCTV visitor IDs are anonymous — they cannot be directly joined to POS receipts using any customer identifier.
- The 5-minute window approximates the checkout process duration: a customer who joins the billing queue will complete their transaction within about 5 minutes under normal conditions.
- Using `BILLING_QUEUE_JOIN` rather than `ZONE_ENTER(CASH_COUNTER)` as the anchor is more precise — it confirms the visitor was actually waiting at the counter, not just passing through the billing zone.
- The window is computed as `[event.timestamp, event.timestamp + 5 min]`. Any POS transaction for the same store that falls in this window results in the visitor being counted as converted.

**Trade-offs accepted:**
- If the store is busy and multiple customers are in the queue simultaneously, a transaction may be matched to the wrong visitor's event. This leads to slight over-counting of converted visitors.
- The 5-minute window is not configurable without a code change. Future work could expose this as an environment variable.

---

## Decision 8: Generalisation — Layout JSON as Single Source of Truth

**Options considered:** Hardcode store IDs in pipeline and scripts, environment variable per store, database-driven store registry, layout JSON

**Choice:** `data/store_layout.json` is the single source of truth. The pipeline, API, and normalization scripts discover stores, cameras, and zones by reading this file at runtime. No store IDs, camera IDs, or zone names are hardcoded anywhere in application or pipeline code.

**Rationale:**
- Adding a new store requires zero code changes: edit the layout JSON, drop clips, drop a POS CSV, run `docker compose up --build`.
- The `--store-id-map` flag in `normalize_data.py` (and `POS_STORE_ID_MAP` env var in Docker) handles the common case where the POS CSV uses a different store ID than the layout, again without any code change.
- The layout JSON is already mounted read-only into both the API and pipeline containers, so it is available to both services without duplication.

**Trade-offs accepted:**
- Editing a JSON file directly is less user-friendly than a web UI for store management. A future admin UI could write to this file on behalf of the user.
- Deleting a store from the layout does not delete its historical events from the database. A manual `DELETE FROM events WHERE store_id = ...` is currently required.
