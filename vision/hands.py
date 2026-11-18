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
from vision.leader import Hand, box_from_points, dedupe_hands

MODEL_DIR = Path(__file__).resolve().parent / "models"
TASK_MODEL = MODEL_DIR / "hand_landmarker.task"


class _Backend:
    def poll(self, bgr, exclude=()):
        """병렬판과 같은 사용법: 매번 바로 처리해서 결과를 돌려준다."""
        return self.detect_all(bgr, exclude)

    def detect(self, bgr, exclude=()):
        """가장 크게 보이는 손 하나의 (HandShape, 신뢰도) 또는 None (예전 호출용)."""
        hands = self.detect_all(bgr, exclude)
        if not hands:
            return None
        hand = max(hands, key=lambda h: h.area)
        return hand.shape, hand.score


class SolutionsHands(_Backend):
    name = "mediapipe-solutions"

    def __init__(self, max_hands: int = 4):
        import mediapipe as mp
        self._hands = mp.solutions.hands.Hands(
            max_num_hands=max_hands, model_complexity=0,
            min_detection_confidence=0.6, min_tracking_confidence=0.5)

    def detect_all(self, bgr, exclude=()):
        """보이는 손 전부의 Hand 목록."""
        h, w = bgr.shape[:2]
        result = self._hands.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        if not result.multi_hand_landmarks:
            return []
        hands = []
        for i, marks in enumerate(result.multi_hand_landmarks):
            lm = [(p.x * w, p.y * h) for p in marks.landmark]
            cls = result.multi_handedness[i].classification[0]
            hands.append(Hand(shape_from_landmarks(lm), float(cls.score), box_from_points(lm),
                              side=cls.label))
        return dedupe_hands(hands)

    def close(self):
        self._hands.close()


class TasksHands(_Backend):
    name = "mediapipe-tasks"

    def __init__(self, model_path: Path = TASK_MODEL, max_hands: int = 4):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
        if not Path(model_path).exists():
            raise FileNotFoundError(f"{model_path} 가 없습니다.")
        self._mp = mp
        options = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.VIDEO, num_hands=max_hands)
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._t0 = time.monotonic()

    def detect_all(self, bgr, exclude=()):
        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int((time.monotonic() - self._t0) * 1000)
        result = self._landmarker.detect_for_video(image, ts_ms)
        hands = []
        for i, marks in enumerate(result.hand_landmarks or []):
            lm = [(p.x * w, p.y * h) for p in marks]
            cat = result.handedness[i][0] if i < len(result.handedness or []) else None
            hands.append(Hand(shape_from_landmarks(lm), float(cat.score) if cat else 0.5,
                              box_from_points(lm), side=cat.category_name if cat else None))
        return dedupe_hands(hands)

    def close(self):
        self._landmarker.close()


class OpenCVHands(_Backend):
    """피부색으로 손 윤곽을 찾아 손가락 사이 골(convexity defect)을 센다.

    조명과 배경에 민감하다. 얼굴이 손으로 잡히지 않도록 exclude에 얼굴 영역을 넘긴다.
    """

    name = "opencv-contour"

    def __init__(self, min_area_ratio: float = 0.02, max_hands: int = 1):
        self.min_area_ratio = min_area_ratio  # 피부색 방식은 가장 큰 덩어리 하나만 본다

    def detect_all(self, bgr, exclude=()):
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
            return []
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < self.min_area_ratio * w * h:
            return []

        hull_idx = cv2.convexHull(contour, returnPoints=False)
        try:
            defects = cv2.convexityDefects(contour, hull_idx)
        except cv2.error:
            defects = None
        bx, by, bw, bh = cv2.boundingRect(contour)
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
        return [Hand(shape_from_count(count), 0.5, (bx, by, bw, bh))]

    def close(self):
        pass


class ParallelHands:
    """손 인식기 여러 개를 스레드로 돌려 처리량을 늘린다.

    MediaPipe Hands는 찾은 손이 max_hands보다 적으면 매 프레임 무거운 손 찾기(palm detection)를
    다시 돌려서 RPi 400에서 손 1~2개일 때 5~7장/초밖에 안 나온다. 한 개가 CPU 1개만 쓰므로
    (4코어 중 1개) 인식기를 workers개 만들어 프레임을 번갈아 맡기면 처리량이 늘어난다.
    실측(11/19, 손 1개/2개): 1개 7.2/5.5장 → 2개 12.2/9.5장 → 3개 15.3/12.0장/초.

    poll(frame)은 쉬는 인식기가 있으면 그 프레임을 맡기고, 새로 끝난 결과가 있으면 돌려준다
    (없으면 None). 결과는 맡긴 순서대로만 받는다(늦게 끝난 옛 프레임 결과는 버림).
    """

    def __init__(self, factory, workers: int = 3):
        import queue
        import threading
        self.name = "parallel"
        self._lock = threading.Lock()
        self._seq = 0
        self._latest_seq = 0
        self._latest = None
        self._fresh = False
        self._running = True
        self._idle = queue.Queue()
        self._workers = []
        for _ in range(workers):
            backend = factory()
            self.name = backend.name
            inbox = queue.Queue(maxsize=1)
            thread = threading.Thread(target=self._loop, args=(backend, inbox), daemon=True)
            self._workers.append((backend, inbox, thread))
            self._idle.put(inbox)
            thread.start()
        self.workers = workers

    def _loop(self, backend, inbox):
        while True:
            job = inbox.get()
            if job is None:
                break
            seq, frame, exclude = job
            try:
                hands = backend.detect_all(frame, exclude=exclude)
            except Exception as exc:  # 한 프레임 실패로 스레드가 죽지 않게
                print(f"[hands] 인식 실패: {exc}")
                hands = []
            with self._lock:
                if seq > self._latest_seq:
                    self._latest_seq, self._latest, self._fresh = seq, hands, True
            self._idle.put(inbox)

    def poll(self, bgr, exclude=()):
        import queue
        try:
            inbox = self._idle.get_nowait()
        except queue.Empty:
            inbox = None
        if inbox is not None:
            self._seq += 1
            inbox.put((self._seq, bgr.copy(), tuple(exclude)))
        with self._lock:
            if not self._fresh:
                return None
            self._fresh = False
            return self._latest

    def detect_all(self, bgr, exclude=()):
        """한 프레임의 결과를 기다려서 돌려준다 (단발 사용)."""
        import time
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            result = self.poll(bgr, exclude)
            if result is not None:
                return result
            time.sleep(0.005)
        return []

    def close(self):
        for _, inbox, thread in self._workers:
            try:
                inbox.put_nowait(None)
            except Exception:
                pass
        for backend, _, thread in self._workers:
            thread.join(timeout=1)
            backend.close()


def create_hand_tracker(prefer: str = "auto", max_hands: int = 4, workers: int = 1):
    """prefer: auto | solutions | tasks | opencv. max_hands: 한 번에 찾을 손 개수.
    workers > 1 이면 ParallelHands로 감싸서 돌려준다 (poll 사용)."""
    order = {"auto": (SolutionsHands, TasksHands, OpenCVHands),
             "solutions": (SolutionsHands,), "tasks": (TasksHands,),
             "opencv": (OpenCVHands,)}[prefer]
    errors = []
    for backend in order:
        try:
            tracker = backend(max_hands=max_hands)
        except Exception as exc:  # 설치 안 됨, 모델 파일 없음, API 없음
            errors.append(f"{backend.name}: {exc}")
            continue
        if workers > 1 and backend is not OpenCVHands:
            tracker.close()
            tracker = ParallelHands(lambda: backend(max_hands=max_hands), workers)
            print(f"[hands] {tracker.name} 사용 (인식기 {workers}개 병렬)")
            return tracker
        print(f"[hands] {tracker.name} 사용")
        return tracker
    raise RuntimeError("손 인식 백엔드를 열 수 없습니다.\n  " + "\n  ".join(errors))


__all__ = ["Hand", "HandShape", "ParallelHands", "create_hand_tracker", "SolutionsHands", "TasksHands", "OpenCVHands"]
