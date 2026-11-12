"""얼굴 검출 → face_count 와 bounding box.

YuNet(node4/models 의 onnx, OpenCV 4.5.4 이상)을 먼저 쓰고, 없으면 Haar Cascade를 쓴다.
돌려주는 좌표는 항상 넘겨받은 프레임(원본 해상도) 기준이다.
"""

from __future__ import annotations

from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
YUNET_MODEL = ROOT / "node4" / "models" / "face_detection_yunet_2023mar.onnx"
HAAR_NAME = "haarcascade_frontalface_default.xml"


def _haar_path() -> str | None:
    candidates = [Path(__file__).resolve().parent / "models" / HAAR_NAME,
                  Path("/usr/share/opencv4/haarcascades") / HAAR_NAME]
    data = getattr(cv2, "data", None)
    if data is not None:
        candidates.insert(0, Path(data.haarcascades) / HAAR_NAME)
    for path in candidates:
        if path.exists():
            return str(path)
    return None


class FaceDetector:
    """스레드마다 따로 만들어 쓴다(OpenCV 검출기는 스레드 안전하지 않다)."""

    def __init__(self, score_threshold: float = 0.7):
        self._yunet = None
        self._haar = None
        if hasattr(cv2, "FaceDetectorYN") and YUNET_MODEL.exists():
            self._yunet = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL), "", (320, 240), score_threshold=score_threshold)
            self.name = "yunet"
            return
        path = _haar_path()
        if path is None:
            raise RuntimeError(
                f"얼굴 검출 모델이 없습니다. {YUNET_MODEL} 또는 vision/models/{HAAR_NAME} 을 두세요.")
        self._haar = cv2.CascadeClassifier(path)
        if self._haar.empty():
            raise RuntimeError(f"Haar Cascade를 읽지 못했습니다: {path}")
        self.name = "haar"

    def detect(self, bgr, max_width: int = 320) -> list[dict]:
        """얼굴 목록 [{"x","y","w","h"}] (왼쪽부터). 검출은 max_width로 줄여서 한다."""
        h, w = bgr.shape[:2]
        scale = min(1.0, max_width / w)
        small = bgr if scale == 1.0 else cv2.resize(bgr, (int(w * scale), int(h * scale)))
        sh, sw = small.shape[:2]

        if self._yunet is not None:
            self._yunet.setInputSize((sw, sh))
            _, found = self._yunet.detect(small)
            boxes = [] if found is None else [row[:4] for row in found]
        else:
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            boxes = self._haar.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5,
                                                minSize=(sw // 12, sw // 12))
        faces = []
        for (x, y, bw, bh) in boxes:
            x0, y0 = max(0.0, x / scale), max(0.0, y / scale)
            faces.append({"x": int(x0), "y": int(y0),
                          "w": int(min(w - x0, bw / scale)), "h": int(min(h - y0, bh / scale))})
        faces.sort(key=lambda f: f["x"])
        return faces


class CountDebouncer:
    """같은 값이 연속으로 나올 때만 인원수 변경으로 인정한다."""

    def __init__(self, hits: int = 3):
        self.hits = hits
        self.value = None
        self._candidate = None
        self._streak = 0

    def update(self, count: int):
        """인원수가 바뀌어 확정되면 새 값을, 아니면 None을 돌려준다."""
        if count == self._candidate:
            self._streak += 1
        else:
            self._candidate = count
            self._streak = 1
        if self._streak >= self.hits and count != self.value:
            self.value = count
            return count
        return None
