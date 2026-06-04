from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from pipeline.detector import Detection


@dataclass
class VisitorSession:
    visitor_id: str
    last_seen: datetime
    last_exit: datetime | None = None
    embedding: list[float] = field(default_factory=list)


class ReIdentifier:
    def __init__(self, threshold: float = 0.75, reentry_window_seconds: int = 300):
        self.threshold = threshold
        self.reentry_window = timedelta(seconds=reentry_window_seconds)
        self.sessions: list[VisitorSession] = []

    def new_visitor_id(self) -> str:
        return f"VIS_{uuid4().hex[:8]}"

    def match_or_create(self, timestamp: datetime, embedding: list[float] | None = None) -> tuple[str, bool]:
        embedding = embedding or []
        # If we have an appearance embedding, try to match against recent sessions
        if embedding:
            import math

            best_cos = -1.0
            best_session = None
            for session in self.sessions:
                if not session.embedding:
                    continue
                a = session.embedding
                b = embedding
                if len(a) != len(b) or not a:
                    continue
                num = sum(x * y for x, y in zip(a, b))
                den_a = math.sqrt(sum(x * x for x in a))
                den_b = math.sqrt(sum(y * y for y in b))
                if den_a <= 0 or den_b <= 0:
                    continue
                cos = num / (den_a * den_b)
                if cos < self.threshold:
                    continue

                # Enforce time-proximity constraint
                time_diff = abs((timestamp - session.last_seen).total_seconds())
                if session.last_exit:
                    time_diff = min(time_diff, abs((timestamp - session.last_exit).total_seconds()))
                if time_diff > self.reentry_window.total_seconds():
                    continue

                if cos > best_cos:
                    best_cos = cos
                    best_session = session

            if best_session is not None:
                is_reentry = bool(best_session.last_exit and abs((timestamp - best_session.last_exit).total_seconds()) <= self.reentry_window.total_seconds())
                best_session.last_seen = timestamp
                best_session.embedding = embedding
                return best_session.visitor_id, is_reentry

        # otherwise create new session
        visitor_id = self.new_visitor_id()
        self.sessions.append(VisitorSession(visitor_id=visitor_id, last_seen=timestamp, embedding=embedding or []))
        return visitor_id, False

    def mark_exit(self, visitor_id: str, timestamp: datetime) -> None:
        for session in self.sessions:
            if session.visitor_id == visitor_id:
                session.last_exit = timestamp
                session.last_seen = timestamp
                return


@dataclass
class Track:
    track_id: int
    bbox: tuple[int, int, int, int]
    centroid: tuple[int, int]
    confidence: float
    missed_frames: int = 0
    visitor_id: str | None = None
    current_zone: str | None = None
    zone_entered_at_ms: int | None = None
    last_dwell_bucket: int = 0
    last_side: int | None = None
    entered: bool = False
    exited: bool = False
    session_seq: int = 0
    is_staff: bool = False
    embedding: list[float] = field(default_factory=list)
    billing_entered_at: datetime | None = None
    seen_frames: int = 1


class CentroidTracker:
    def __init__(self, max_distance: int = 120, max_missed_frames: int = 12):
        self.max_distance = max_distance
        self.max_missed_frames = max_missed_frames
        self.next_track_id = 1
        self.tracks: dict[int, Track] = {}
        self.last_expired_tracks: list[Track] = []

    def update(self, detections: list[Detection]) -> list[Track]:
        self.last_expired_tracks = []
        unmatched_track_ids = set(self.tracks)
        assigned_detection_ids: set[int] = set()

        for det_idx, detection in enumerate(detections):
            if detection.track_id is None:
                continue
            track_id = detection.track_id
            if track_id in self.tracks:
                track = self.tracks[track_id]
                track.bbox = detection.bbox
                track.centroid = detection.centroid
                track.confidence = detection.confidence
                # propagate optional appearance and staff flag from detection
                if hasattr(detection, 'embedding') and detection.embedding:
                    track.embedding = detection.embedding
                if hasattr(detection, 'is_staff'):
                    track.is_staff = bool(detection.is_staff)
                track.missed_frames = 0
                track.seen_frames += 1
                unmatched_track_ids.discard(track_id)
            else:
                track = Track(
                    track_id=track_id,
                    bbox=detection.bbox,
                    centroid=detection.centroid,
                    confidence=detection.confidence,
                )
                if hasattr(detection, 'embedding') and detection.embedding:
                    track.embedding = detection.embedding
                if hasattr(detection, 'is_staff'):
                    track.is_staff = bool(detection.is_staff)
                self.tracks[track_id] = track
            assigned_detection_ids.add(det_idx)

        for det_idx, detection in enumerate(detections):
            if detection.track_id is not None:
                continue
            best_track_id = None
            best_distance = self.max_distance + 1
            cx, cy = detection.centroid
            for track_id in list(unmatched_track_ids):
                tx, ty = self.tracks[track_id].centroid
                distance = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
                if distance < best_distance:
                    best_distance = distance
                    best_track_id = track_id

            if best_track_id is None:
                continue

            track = self.tracks[best_track_id]
            track.bbox = detection.bbox
            track.centroid = detection.centroid
            track.confidence = detection.confidence
            if hasattr(detection, 'embedding') and detection.embedding:
                track.embedding = detection.embedding
            if hasattr(detection, 'is_staff'):
                track.is_staff = bool(detection.is_staff)
            track.missed_frames = 0
            track.seen_frames += 1
            unmatched_track_ids.remove(best_track_id)
            assigned_detection_ids.add(det_idx)

        for track_id in unmatched_track_ids:
            self.tracks[track_id].missed_frames += 1

        for det_idx, detection in enumerate(detections):
            if det_idx in assigned_detection_ids:
                continue
            track = Track(
                track_id=self.next_track_id,
                bbox=detection.bbox,
                centroid=detection.centroid,
                confidence=detection.confidence,
            )
            if hasattr(detection, 'embedding') and detection.embedding:
                track.embedding = detection.embedding
            if hasattr(detection, 'is_staff'):
                track.is_staff = bool(detection.is_staff)
            self.tracks[self.next_track_id] = track
            self.next_track_id += 1

        expired = [
            track_id
            for track_id, track in self.tracks.items()
            if track.missed_frames > self.max_missed_frames
        ]
        for track_id in expired:
            self.last_expired_tracks.append(self.tracks.pop(track_id))

        return [track for track in self.tracks.values() if track.missed_frames == 0]
