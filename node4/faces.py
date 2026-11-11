"""얼굴 위치와 미소 점수 보조.

정면 노드가 faces, smile_score를 보내면 그 값을 그대로 쓴다.
보조 사진처럼 값이 없을 때만 여기서 채운다.

- OpenCV 4: Haar Cascade
- OpenCV 5: YuNet (CascadeClassifier가 빠졌다)
둘 다 없으면 빈 결과로 두고, 합성은 가운데 크롭으로 계속한다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

_cv2 = None
_cv2_checked = False
_face_cascade = None
_smile_cascade = None
_yunet = None

MODEL_PATH = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"


def _libs():
    global _cv2, _cv2_checked
    if _cv2_checked:
        return _cv2
    _cv2_checked = True
    try:
        import cv2
        import numpy as np
    except ImportError:
        _cv2 = None
        return None
    _cv2 = (cv2, np)
    return _cv2


def _cascade(name: str):
    libs = _libs()
    if libs is None or not hasattr(libs[0], "CascadeClassifier"):
        return None
    cv2, _np = libs
    path = cv2.data.haarcascades + name
    cascade = cv2.CascadeClassifier(path)
    if cascade.empty():
        return None
    return cascade


def detect_faces(image: Image.Image) -> list[dict]:
    """얼굴 bounding box. 검출기가 없으면 빈 목록."""
    yunet_faces = _detect_yunet(image)
    if yunet_faces is not None:
        return yunet_faces
    return _detect_haar(image)


def estimate_smile_score(image: Image.Image, faces: list[dict] | None) -> float | None:
    """Haar 미소 검출이 있을 때만 0~100을 추정한다. 없으면 None."""
    global _smile_cascade
    libs = _libs()
    if libs is None or not faces:
        return None
    cv2, np = libs
    if _smile_cascade is None:
        _smile_cascade = _cascade("haarcascade_smile.xml")
    if _smile_cascade is None:
        return None

    face = max(faces, key=lambda item: int(item["w"]) * int(item["h"]))
    x, y, w, h = (int(face["x"]), int(face["y"]), int(face["w"]), int(face["h"]))
    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    y0 = max(0, y + int(h * 0.45))
    x0 = max(0, x)
    x1 = min(gray.shape[1], x + w)
    y1 = min(gray.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    roi = gray[y0:y1, x0:x1]
    smiles = _smile_cascade.detectMultiScale(roi, scaleFactor=1.7, minNeighbors=12, minSize=(16, 16))
    if len(smiles) == 0:
        return 32.0
    return float(min(100, 58 + 14 * len(smiles)))


def _detect_haar(image: Image.Image) -> list[dict]:
    global _face_cascade
    libs = _libs()
    if libs is None:
        return []
    cv2, np = libs
    if _face_cascade is None:
        _face_cascade = _cascade("haarcascade_frontalface_default.xml")
    if _face_cascade is None:
        return []
    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    rects = _face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    return [{"x": int(x), "y": int(y), "w": int(w), "h": int(h)} for (x, y, w, h) in rects]


def _detect_yunet(image: Image.Image) -> list[dict] | None:
    """모델이 있고 FaceDetectorYN이 있으면 검출 결과. 사용할 수 없으면 None."""
    global _yunet
    libs = _libs()
    if libs is None or not hasattr(libs[0], "FaceDetectorYN") or not MODEL_PATH.is_file():
        return None
    cv2, np = libs
    rgb = np.array(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if _yunet is None:
        _yunet = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (width, height), 0.8, 0.3, 5000)
    _yunet.setInputSize((width, height))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    _retval, found = _yunet.detect(bgr)
    if found is None:
        return []
    faces = []
    for row in found:
        x, y, w, h = row[:4]
        faces.append({
            "x": max(0, int(x)),
            "y": max(0, int(y)),
            "w": max(1, int(w)),
            "h": max(1, int(h)),
        })
    return faces
