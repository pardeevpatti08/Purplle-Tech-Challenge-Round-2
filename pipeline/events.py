from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pipeline.emit import build_event
from pipeline.layout import CameraConfig, StoreLayout
from pipeline.pos import PosTransaction
from pipeline.tracker import ReIdentifier, Track


@dataclass
class EventProcessor:
    layout: StoreLayout
    camera: CameraConfig
    clip_start: datetime
    fps: float
    reidentifier: ReIdentifier = field(default_factory=ReIdentifier)
    pos_transactions: list[PosTransaction] = field(default_factory=list)
    active_billing_visitors: set[str] = field(default_factory=set)

    def frame_time(self, frame_idx: int) -> datetime:
        return self.clip_start + timedelta(seconds=frame_idx / max(self.fps, 1.0))

    @property
    def store_id(self) -> str:
        return self.camera.store_id or self.layout.store_id

    def process_track(self, track: Track, frame_idx: int) -> list[dict]:
        timestamp = self.frame_time(frame_idx)
        if track.visitor_id is None:
            embedding = getattr(track, 'embedding', None) or []
            track.visitor_id, _ = self.reidentifier.match_or_create(timestamp, embedding=embedding)

        events: list[dict] = []
        zone_id = self.zone_for_track(track)
        if (
            self.camera.role == "ENTRY"
            and self.camera.entry_mode == "presence"
            and not track.entered
            and track.seen_frames >= self.camera.entry_confirmation_frames
        ):
            track.entered = True
            track.session_seq += 1
            events.append(
                self.build(
                    track=track,
                    event_type="ENTRY",
                    timestamp=timestamp,
                    zone_id=None,
                    dwell_ms=0,
                )
            )
        events.extend(self.entry_exit_events(track, timestamp))
        events.extend(self.zone_events(track, zone_id, frame_idx, timestamp))
        return events

    def close_track(self, track: Track, frame_idx: int) -> list[dict]:
        timestamp = self.frame_time(frame_idx)
        events: list[dict] = []
        if track.current_zone:
            track.session_seq += 1
            events.append(
                self.build(
                    track=track,
                    event_type="ZONE_EXIT",
                    timestamp=timestamp,
                    zone_id=track.current_zone,
                    dwell_ms=0,
                )
            )
            if self.is_billing_zone(track.current_zone) and not self.has_following_purchase(track):
                track.session_seq += 1
                events.append(
                    self.build(
                        track=track,
                        event_type="BILLING_QUEUE_ABANDON",
                        timestamp=timestamp,
                        zone_id=track.current_zone,
                        dwell_ms=0,
                    )
                )
        if self.is_billing_zone(track.current_zone) and track.visitor_id:
            self.active_billing_visitors.discard(track.visitor_id)
        return events

    def zone_for_track(self, track: Track) -> str | None:
        role = self.camera.role
        if role == "BACK_AREA":
            track.is_staff = True
            return self.layout.coverage_zone_for_type(self.camera, "back_area", "staff_or_storage")
        polygon_zone = self.layout.zone_for_point(self.store_id, self.camera.coverage_zones, track.centroid)
        if polygon_zone:
            return polygon_zone
        if role == "ENTRY":
            return self.layout.coverage_zone_for_type(self.camera, "threshold")
        if role == "BILLING":
            return self.layout.coverage_zone_for_type(self.camera, "billing")
        for zone_id in self.camera.coverage_zones:
            if not self.layout.zone_has_type(self.store_id, zone_id, "floor"):
                return zone_id
        return self.layout.coverage_zone_for_type(self.camera, "floor")

    def is_billing_zone(self, zone_id: str | None) -> bool:
        return self.layout.zone_has_type(self.store_id, zone_id, "billing")

    def entry_exit_events(self, track: Track, timestamp: datetime) -> list[dict]:
        if self.camera.role != "ENTRY" or self.camera.entry_mode != "threshold" or not self.camera.entry_threshold:
            return []

        x, y = track.centroid
        line = self.camera.entry_threshold["line"]
        is_vertical = line[0][0] == line[1][0]
        threshold = int(line[0][0] if is_vertical else line[0][1])
        position = x if is_vertical else y
        side = 1 if position >= threshold else -1
        events: list[dict] = []
        if track.last_side is None:
            track.last_side = side
            return events
        if side == track.last_side:
            return events

        inbound_direction = self.camera.entry_threshold.get("inbound_direction", "right")
        if inbound_direction in {"right", "down"}:
            crossed_inbound = side > track.last_side
        else:
            crossed_inbound = side < track.last_side
        event_type = "ENTRY" if crossed_inbound else "EXIT"
        if event_type == "ENTRY" and track.entered:
            event_type = "REENTRY"
        if event_type == "EXIT":
            self.reidentifier.mark_exit(track.visitor_id or "", timestamp)
            track.exited = True
        if event_type in {"ENTRY", "REENTRY"}:
            track.entered = True

        track.last_side = side
        track.session_seq += 1
        events.append(
            self.build(
                track=track,
                event_type=event_type,
                timestamp=timestamp,
                zone_id=None,
                dwell_ms=0,
            )
        )
        return events

    def zone_events(self, track: Track, zone_id: str | None, frame_idx: int, timestamp: datetime) -> list[dict]:
        events: list[dict] = []
        elapsed_ms = int(frame_idx / max(self.fps, 1.0) * 1000)
        if zone_id != track.current_zone:
            if track.current_zone:
                track.session_seq += 1
                events.append(
                    self.build(
                        track=track,
                        event_type="ZONE_EXIT",
                        timestamp=timestamp,
                        zone_id=track.current_zone,
                        dwell_ms=0,
                    )
                )
                if self.is_billing_zone(track.current_zone) and track.visitor_id:
                    if not self.has_following_purchase(track):
                        track.session_seq += 1
                        events.append(
                            self.build(
                                track=track,
                                event_type="BILLING_QUEUE_ABANDON",
                                timestamp=timestamp,
                                zone_id=track.current_zone,
                                dwell_ms=0,
                            )
                        )
                    self.active_billing_visitors.discard(track.visitor_id)

            track.current_zone = zone_id
            track.zone_entered_at_ms = elapsed_ms
            track.last_dwell_bucket = 0
            if zone_id:
                track.session_seq += 1
                events.append(
                    self.build(
                        track=track,
                        event_type="ZONE_ENTER",
                        timestamp=timestamp,
                        zone_id=zone_id,
                        dwell_ms=0,
                    )
                )
                if self.is_billing_zone(zone_id) and track.visitor_id:
                    self.active_billing_visitors.add(track.visitor_id)
                    track.billing_entered_at = timestamp
                    track.session_seq += 1
                    events.append(
                        self.build(
                            track=track,
                            event_type="BILLING_QUEUE_JOIN",
                            timestamp=timestamp,
                            zone_id=zone_id,
                            dwell_ms=0,
                            queue_depth=len(self.active_billing_visitors),
                        )
                    )
            return events

        if zone_id and track.zone_entered_at_ms is not None:
            dwell_ms = elapsed_ms - track.zone_entered_at_ms
            dwell_bucket = dwell_ms // 30000
            if dwell_bucket > track.last_dwell_bucket:
                track.last_dwell_bucket = dwell_bucket
                track.session_seq += 1
                events.append(
                    self.build(
                        track=track,
                        event_type="ZONE_DWELL",
                        timestamp=timestamp,
                        zone_id=zone_id,
                        dwell_ms=dwell_ms,
                    )
                )
        return events

    def has_following_purchase(self, track: Track) -> bool:
        if track.billing_entered_at is None:
            return False
        window_end = track.billing_entered_at + timedelta(minutes=5)
        store_id = self.layout.canonical_store_id(self.store_id)
        return any(
            self.layout.canonical_store_id(transaction.store_id) == store_id
            and track.billing_entered_at <= transaction.timestamp <= window_end
            for transaction in self.pos_transactions
        )

    def build(
        self,
        *,
        track: Track,
        event_type: str,
        timestamp: datetime,
        zone_id: str | None,
        dwell_ms: int,
        queue_depth: int | None = None,
    ) -> dict:
        return build_event(
            store_id=self.store_id,
            camera_id=self.camera.camera_id,
            visitor_id=track.visitor_id or f"TRACK_{track.track_id}",
            event_type=event_type,
            timestamp=timestamp,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=track.is_staff,
            confidence=track.confidence,
            queue_depth=queue_depth,
            sku_zone=self.layout.zone_name(self.store_id, zone_id),
            session_seq=track.session_seq,
        )
