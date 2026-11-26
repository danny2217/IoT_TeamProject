"""MediaPipe Face Mesh 공통 래퍼 — 미소 점수(smile.py)와 시선 판단(gaze.py)이 같이 쓴다.

얼굴 상자(FaceDetector 결과)마다 주변을 잘라 일정 크기로 키운 뒤 Face Mesh를 돌린다.
- 사진 전체를 줄여서 한 번에 돌리면 1m 밖 작은 얼굴은 Face Mesh가 못 찾았다 (점수 None).
- 얼굴만 잘라 키우면 멀리 있는 얼굴도 랜드마크가 나오고, 얼굴 상자와 결과 짝짓기도 필요 없다.

랜드마크는 478개 (refine=True: 468~477 눈동자 포함), 원본 이미지 픽셀 좌표 (x, y).
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

FACE_TASK_MODEL = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
CROP_MARGIN = 0.35      # 얼굴 상자 바깥으로 더 자를 비율 (턱·이마가 잘리지 않게)
CROP_SIZE = 256         # 잘라 낸 얼굴을 이 크기 정사각형으로 맞춘다


def crop_box(face: dict, width: int, height: int, margin: float = CROP_MARGIN) -> tuple[int, int, int, int]:
    """얼굴 상자를 정사각형으로 넓힌 (x0, y0, x1, y1). 이미지 밖은 잘라 낸다."""
    cx, cy = face["x"] + face["w"] / 2, face["y"] + face["h"] / 2
    side = max(face["w"], face["h"]) * (1 + 2 * margin)
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x1, y1 = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
    return x0, y0, x1, y1


def _tasks_runs_here() -> bool:
    """FaceLandmarker를 별도 프로세스에서 한 번 만들어 본다.

    mediapipe 1.x 휠은 AES 명령이 있는 CPU용이라 Pi 3B·Pi 400에서는 예외가 아니라
    Illegal instruction으로 프로세스가 통째로 죽는다. 그래서 try로는 못 잡고 따로 돌려 본다.
    """
    import subprocess
    import sys
    code = ("from mediapipe.tasks import python as p; from mediapipe.tasks.python import vision as v;"
            f"v.FaceLandmarker.create_from_options(v.FaceLandmarkerOptions("
            f"base_options=p.BaseOptions(model_asset_path={str(FACE_TASK_MODEL)!r})))")
    try:
        return subprocess.run([sys.executable, "-c", code], capture_output=True,
                              timeout=60).returncode == 0
    except Exception:
        return False


class FaceMesh:
    """스레드마다 따로 만들어 쓰는 게 좋다 (안에서 잠그므로 같이 써도 틀리지는 않음)."""

    def __init__(self, refine: bool = True, max_faces: int = 6):
        self._lock = threading.Lock()
        self._mesh = None
        self._tasks = None
        errors = []
        try:
            import mediapipe as mp
            self._mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True, max_num_faces=max_faces,
                refine_landmarks=refine, min_detection_confidence=0.5)
            self.name = "facemesh"
            return
        except Exception as exc:  # 설치 안 됨 / 구형 API 없음 (mediapipe 0.10.20 이후)
            errors.append(f"solutions: {exc}")
        try:
            if not FACE_TASK_MODEL.exists():
                raise FileNotFoundError(f"{FACE_TASK_MODEL} 가 없습니다")
            if not _tasks_runs_here():
                raise RuntimeError("이 CPU에서 MediaPipe Tasks가 죽음 (mediapipe 1.x: AES 명령 없음)")
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            options = mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(FACE_TASK_MODEL)),
                running_mode=mp_vision.RunningMode.IMAGE, num_faces=max_faces)
            self._tasks = mp_vision.FaceLandmarker.create_from_options(options)
            self._mp = mp
            self.name = "facemesh-tasks"
            return
        except Exception as exc:
            errors.append(f"tasks: {exc}")
        raise RuntimeError("Face Mesh 사용 불가: " + "; ".join(errors))

    # ------------------------------------------------------------------ 실행
    def _run(self, rgb) -> list:
        """얼굴마다 정규화 좌표(0~1) 랜드마크 목록."""
        if self._mesh is not None:
            result = self._mesh.process(rgb)
            return [m.landmark for m in result.multi_face_landmarks or []]
        import numpy as np
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        return list(self._tasks.detect(image).face_landmarks or [])

    def whole(self, bgr, max_width: int = 960) -> list[list[tuple[float, float]]]:
        """사진 전체에서 찾은 얼굴들의 랜드마크 (원본 픽셀 좌표)."""
        import cv2
        h, w = bgr.shape[:2]
        scale = min(1.0, max_width / w)
        small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale < 1 else bgr
        sh, sw = small.shape[:2]
        with self._lock:
            found = self._run(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        return [[(p.x * sw / scale, p.y * sh / scale) for p in marks] for marks in found]

    def for_faces(self, bgr, faces: list[dict], size: int = CROP_SIZE) -> list:
        """얼굴 상자마다 랜드마크 (원본 픽셀 좌표), 못 찾으면 None."""
        import cv2
        h, w = bgr.shape[:2]
        out = []
        for face in faces:
            x0, y0, x1, y1 = crop_box(face, w, h)
            if x1 - x0 < 8 or y1 - y0 < 8:
                out.append(None)
                continue
            crop = cv2.resize(bgr[y0:y1, x0:x1], (size, size))
            with self._lock:
                found = self._run(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            if not found:
                out.append(None)
                continue
            sx, sy = (x1 - x0) / size, (y1 - y0) / size
            # 옆 사람 얼굴이 같이 잘렸을 수 있으므로 잘라 낸 영역 가운데에 가장 가까운 얼굴
            best = min(found, key=lambda m: math.dist((m[1].x, m[1].y), (0.5, 0.5)))
            out.append([(x0 + p.x * size * sx, y0 + p.y * size * sy) for p in best])
        return out

    def close(self) -> None:
        if self._mesh is not None:
            self._mesh.close()
        if self._tasks is not None:
            self._tasks.close()
