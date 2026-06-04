from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from pipeline.staff import classify_staff_by_hsv


@dataclass(frozen=True)
class Detection:
    bbox: tuple[int, int, int, int]
    confidence: float
    label: str = "person"
    track_id: int | None = None
    embedding: list[float] | None = None
    is_staff: bool = False

    @property
    def centroid(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)


class PersonDetector(Protocol):
    name: str

    def detect(self, frame: np.ndarray, frame_idx: int) -> list[Detection]:
        ...


class YoloPersonDetector:
    name = "yolo-bytetrack"

    def __init__(self, model_name: str, staff_hsv_ranges: list[list[int]] | None = None):
        default_config = Path(__file__).resolve().parents[1] / ".yolo"
        os.environ.setdefault("YOLO_CONFIG_DIR", str(default_config))
        from ultralytics import YOLO

        self.model = YOLO(model_name)
        self.staff_hsv_ranges = [tuple(values) for values in (staff_hsv_ranges or [])]

    def detect(self, frame: np.ndarray, frame_idx: int) -> list[Detection]:
        results = self.model.track(
            frame,
            classes=[0],
            verbose=False,
            conf=0.40,
            persist=True,
            tracker="bytetrack.yaml",
        )
        detections: list[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                track_id = int(box.id[0]) if box.id is not None else None
                # compute a simple HSV torso histogram as a lightweight appearance embedding
                h = frame[max(0, y1):min(frame.shape[0], y2), max(0, x1):min(frame.shape[1], x2)]
                embedding = None
                is_staff = False
                try:
                    import cv2

                    th = h.shape[0]
                    if th > 4 and h.shape[1] > 4:
                        torso = h[th // 3 : (th * 2) // 3, :]
                        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
                        hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
                        cv2.normalize(hist, hist)
                        embedding = hist.flatten().astype(float).tolist()
                        mean_h = float(hsv[..., 0].mean())
                        mean_s = float(hsv[..., 1].mean())
                        mean_v = float(hsv[..., 2].mean())
                        is_staff = classify_staff_by_hsv((mean_h, mean_s, mean_v), self.staff_hsv_ranges)
                except Exception:
                    embedding = None
                detections.append(
                    Detection((x1, y1, x2, y2), float(box.conf[0]), track_id=track_id, embedding=embedding, is_staff=is_staff)
                )
        return detections


class MotionPersonDetector:
    name = "motion"

    def __init__(self, min_area: int = 1800, staff_hsv_ranges: list[list[int]] | None = None):
        self.subtractor = cv2.createBackgroundSubtractorMOG2(
            history=180,
            varThreshold=32,
            detectShadows=True,
        )
        self.min_area = min_area

    def detect(self, frame: np.ndarray, frame_idx: int) -> list[Detection]:
        mask = self.subtractor.apply(frame)
        mask = cv2.threshold(mask, 220, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.medianBlur(mask, 5)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[Detection] = []
        height, width = frame.shape[:2]
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self.min_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if w < 24 or h < 48:
                continue
            if w > width * 0.85 or h > height * 0.95:
                continue
            aspect = h / max(w, 1)
            if aspect < 0.7:
                continue
            confidence = min(0.9, max(0.25, area / 35000))
            crop = frame[y : y + h, x : x + w]
            torso = crop[h // 3 : (h * 2) // 3, :]
            embedding = None
            if torso.size:
                hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                embedding = hist.flatten().astype(float).tolist()
            detections.append(Detection((x, y, x + w, y + h), confidence, embedding=embedding, is_staff=False))
        return detections


def create_detector(
    model_name: str,
    prefer_yolo: bool = True,
    staff_hsv_ranges: list[list[int]] | None = None,
) -> PersonDetector:
    if prefer_yolo:
        try:
            return YoloPersonDetector(model_name, staff_hsv_ranges)
        except Exception:
            pass
    return MotionPersonDetector(staff_hsv_ranges=staff_hsv_ranges)
