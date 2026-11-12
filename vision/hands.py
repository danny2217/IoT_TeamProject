"""손 인식 백엔드. 설치된 것 중 쓸 수 있는 것을 순서대로 고른다.

1. MediaPipe 구형 API (mp.solutions.hands)        — Python 3.11 가상환경
2. MediaPipe Tasks API (HandLandmarker)           — vision/models/hand_landmarker.task 필요
3. OpenCV 피부색 + convexity defects (제안서 6.3 대체안) — 엄지 올리기는 구분 못 함
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import cv2
import numpy as np

from vision.gestures import HandShape, shape_from_count, shape_from_landmarks

MODEL_DIR = Path(__file__).resolve().parent / "models"
TASK_MODEL = MODEL_DIR / "hand_landmarker.task"


class SolutionsHands:
    name = "mediapipe-solutions"

    def __init__(self):
        import mediapipe as mp
        self._hands = mp.solutions.hands.Hands(
            max_num_hands=1, model_complexity=0,
            min_detection_confidence=0.6, min_tracking_confidence=0.5)

    def detect(self, bgr, exclude=()):
        """(HandShape, 신뢰도) 또는 None."""
        h, w = bgr.shape[:2]
        result = self._hands.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        if not result.multi_hand_landmarks:
            return None
        lm = [(p.x * w, p.y * h) for p in result.multi_hand_landmarks[0].landmark]
        score = result.multi_handedness[0].classification[0].score
        return shape_from_landmarks(lm), float(score)

    def close(self):
        self._hands.close()


class TasksHands:
    name = "mediapipe-tasks"

    def __init__(self, model_path: Path = TASK_MODEL):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
        if not Path(model_path).exists():
            raise FileNotFoundError(f"{model_path} 가 없습니다.")
        self._mp = mp
        options = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.VIDEO, num_hands=1)
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._t0 = time.monotonic()

    def detect(self, bgr, exclude=()):
        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int((time.monotonic() - self._t0) * 1000)
        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.hand_landmarks:
            return None
        lm = [(p.x * w, p.y * h) for p in result.hand_landmarks[0]]
        score = result.handedness[0][0].score if result.handedness else 0.5
        return shape_from_landmarks(lm), float(score)

    def close(self):
        self._landmarker.close()


class OpenCVHands:
    """피부색으로 손 윤곽을 찾아 손가락 사이 골(convexity defect)을 센다.

    조명과 배경에 민감하다. 얼굴이 손으로 잡히지 않도록 exclude에 얼굴 영역을 넘긴다.
    """

    name = "opencv-contour"

    def __init__(self, min_area_ratio: float = 0.02):
        self.min_area_ratio = min_area_ratio

    def detect(self, bgr, exclude=()):
        h, w = bgr.shape[:2]
        ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, (0, 135, 85), (255, 180, 135))
        for (x, y, fw, fh) in exclude:  # 얼굴과 목을 지운다
            x0, x1 = max(0, int(x - fw * 0.2)), min(w, int(x + fw * 1.2))
            y0, y1 = max(0, int(y - fh * 0.3)), min(h, int(y + fh * 1.6))
            mask[y0:y1, x0:x1] = 0
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < self.min_area_ratio * w * h:
            return None

        hull_idx = cv2.convexHull(contour, returnPoints=False)
        try:
            defects = cv2.convexityDefects(contour, hull_idx)
        except cv2.error:
            defects = None
        _, _, bw, bh = cv2.boundingRect(contour)
        gaps = 0
        if defects is not None:
            for s, e, f, depth in defects[:, 0]:
                start, end, far = contour[s][0], contour[e][0], contour[f][0]
                a = math.dist(start, end)
                b = math.dist(start, far)
                c = math.dist(end, far)
                if b == 0 or c == 0:
                    continue
                angle = math.degrees(math.acos(max(-1.0, min(1.0, (b * b + c * c - a * a) / (2 * b * c)))))
                if angle < 80 and depth / 256.0 > 0.2 * bh:
                    gaps += 1
        if gaps > 0:
            count = min(gaps + 1, 5)
        else:
            hull_area = cv2.contourArea(cv2.convexHull(contour)) or 1.0
            count = 1 if area / hull_area < 0.8 and bh > bw * 1.3 else 0
        return shape_from_count(count), 0.5

    def close(self):
        pass


def create_hand_tracker(prefer: str = "auto"):
    """prefer: auto | solutions | tasks | opencv"""
    order = {"auto": (SolutionsHands, TasksHands, OpenCVHands),
             "solutions": (SolutionsHands,), "tasks": (TasksHands,),
             "opencv": (OpenCVHands,)}[prefer]
    errors = []
    for backend in order:
        try:
            tracker = backend()
        except Exception as exc:  # 설치 안 됨, 모델 파일 없음, API 없음
            errors.append(f"{backend.name}: {exc}")
            continue
        print(f"[hands] {tracker.name} 사용")
        return tracker
    raise RuntimeError("손 인식 백엔드를 열 수 없습니다.\n  " + "\n  ".join(errors))


__all__ = ["HandShape", "create_hand_tracker", "SolutionsHands", "TasksHands", "OpenCVHands"]
