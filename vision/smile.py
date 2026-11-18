"""미소 지수(smile_score, 0~100) — Best Smile 컷 고르기용.

1. MediaPipe Face Mesh (제안서 방식): 입꼬리 랜드마크로 계산
   - 구형 API(mp.solutions.face_mesh) 또는 새 Tasks API(FaceLandmarker, models/face_landmarker.task)
     둘 다 같은 468개 랜드마크라 점수가 같다
   - 입 너비 / 두 눈 바깥 끝 거리     → 웃으면 입이 옆으로 넓어진다
   - 입꼬리 높이 (입 중앙 대비)       → 웃으면 입꼬리가 올라간다
2. Face Mesh가 없으면 OpenCV Haar 미소 검출 (Node 4 대체 방식과 같은 점수표)

사진 한 장의 점수 = 얼굴들 점수의 평균 (단체 사진은 모두가 웃은 컷이 Best).
점수 상수(*_LO/*_HI)는 실사진으로 맞춘다:
    python -m vision.smile 사진1.jpg 사진2.jpg     # 얼굴별 특징값과 점수 출력
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

# Face Mesh 랜드마크 번호
MOUTH_LEFT, MOUTH_RIGHT = 61, 291
LIP_UPPER, LIP_LOWER = 13, 14
EYE_LEFT_OUT, EYE_RIGHT_OUT = 33, 263

# 실기에서 조정할 값 (두 눈 바깥 끝 거리를 1로 본 비율)
# 2026-11-19 Node 2(C922, 얼굴을 약간 아래에서 찍는 각도) 실사진 9장으로 맞춤
#   무표정 입 너비 0.52 / 입꼬리 -0.038,  살짝 0.58 / -0.021,  활짝 0.67 / +0.002
# 카메라 높이가 바뀌면 입꼬리 기준이 달라지므로 설치 후 `python -m vision.smile` 로 다시 확인할 것.
# 한 세션 안에서는 같은 카메라·같은 사람이라 순위(Best Smile 고르기)는 그대로 유효하다.
WIDTH_LO, WIDTH_HI = 0.52, 0.68    # 입 너비: 무표정 ~ 활짝
LIFT_LO, LIFT_HI = -0.04, 0.01     # 입꼬리 높이: 무표정 ~ 활짝
WIDTH_WEIGHT = 0.55                # 나머지는 입꼬리 높이

FACE_TASK_MODEL = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
HAAR_NONE, HAAR_BASE, HAAR_STEP = 32.0, 58.0, 14.0   # node4/faces.py 와 같은 점수표


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def mouth_features(lm) -> tuple[float, float]:
    """(입 너비 비율, 입꼬리 높이 비율). lm은 Face Mesh 랜드마크 (x, y) 픽셀, y는 아래로 증가."""
    eye = math.dist(lm[EYE_LEFT_OUT], lm[EYE_RIGHT_OUT]) or 1.0
    width = math.dist(lm[MOUTH_LEFT], lm[MOUTH_RIGHT]) / eye
    center_y = (lm[LIP_UPPER][1] + lm[LIP_LOWER][1]) / 2
    corner_y = (lm[MOUTH_LEFT][1] + lm[MOUTH_RIGHT][1]) / 2
    lift = (center_y - corner_y) / eye          # 입꼬리가 입 중앙보다 위면 +
    return width, lift


def score_from_landmarks(lm) -> int:
    width, lift = mouth_features(lm)
    w = _clamp01((width - WIDTH_LO) / (WIDTH_HI - WIDTH_LO))
    l = _clamp01((lift - LIFT_LO) / (LIFT_HI - LIFT_LO))
    return round(100 * (WIDTH_WEIGHT * w + (1 - WIDTH_WEIGHT) * l))


def score_from_haar_count(count: int) -> float:
    return HAAR_NONE if count == 0 else min(100.0, HAAR_BASE + HAAR_STEP * count)


def photo_score(scores) -> int | None:
    """얼굴별 점수 → 사진 점수(평균). 얼굴이 없으면 None."""
    scores = [s for s in scores if s is not None]
    return round(sum(scores) / len(scores)) if scores else None


def match_to_faces(mesh_centers, faces) -> list[int | None]:
    """얼굴 상자마다 그 안에 중심이 있는 Face Mesh 얼굴 번호 (없으면 None)."""
    out = []
    for f in faces:
        hit = None
        for i, (cx, cy) in enumerate(mesh_centers):
            if f["x"] <= cx <= f["x"] + f["w"] and f["y"] <= cy <= f["y"] + f["h"]:
                hit = i
                break
        out.append(hit)
    return out


def _tasks_runs_here() -> bool:
    """FaceLandmarker를 별도 프로세스에서 한 번 만들어 본다.

    mediapipe 1.x 휠은 AES 명령이 있는 CPU용이라 Pi 3B에서는 예외가 아니라
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


class SmileScorer:
    """촬영 스레드에서 사진 한 장마다 부른다. 안에서 잠그므로 여러 스레드에서 써도 된다."""

    def __init__(self, prefer: str = "auto", max_faces: int = 6, max_width: int = 960):
        self.max_width = max_width
        self._lock = threading.Lock()
        self._mesh = None
        self._tasks = None
        self._haar = None
        self.name = "none"
        if prefer in ("auto", "mesh"):
            errors = []
            try:
                import mediapipe as mp
                self._mesh = mp.solutions.face_mesh.FaceMesh(
                    static_image_mode=True, max_num_faces=max_faces,
                    refine_landmarks=False, min_detection_confidence=0.5)
                self.name = "facemesh"
                return
            except Exception as exc:  # 설치 안 됨 / 구형 API 없음 (mediapipe 0.10.20 이후)
                errors.append(f"solutions: {exc}")
            try:
                if not FACE_TASK_MODEL.exists():
                    raise FileNotFoundError(f"{FACE_TASK_MODEL} 가 없습니다")
                if not _tasks_runs_here():
                    raise RuntimeError("이 CPU에서 MediaPipe Tasks가 죽음 (Pi 3B + mediapipe 1.x: AES 명령 없음)")
                import mediapipe as mp
                from mediapipe.tasks import python as mp_python
                from mediapipe.tasks.python import vision as mp_vision
                if not FACE_TASK_MODEL.exists():
                    raise FileNotFoundError(f"{FACE_TASK_MODEL} 가 없습니다")
                options = mp_vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(FACE_TASK_MODEL)),
                    running_mode=mp_vision.RunningMode.IMAGE, num_faces=max_faces)
                self._tasks = mp_vision.FaceLandmarker.create_from_options(options)
                self._mp = mp
                self.name = "facemesh-tasks"
                return
            except Exception as exc:
                errors.append(f"tasks: {exc}")
            if prefer == "mesh":
                raise RuntimeError("Face Mesh 사용 불가: " + "; ".join(errors))
            print(f"[smile] Face Mesh 사용 불가 ({'; '.join(errors)}) → Haar 미소 검출")
        import cv2
        path = Path(cv2.data.haarcascades) / "haarcascade_smile.xml" if hasattr(cv2, "data") else None
        if path is not None and path.exists():
            self._haar = cv2.CascadeClassifier(str(path))
            self.name = "haar"
        else:
            print("[smile] 미소 검출 모델이 없어 smile_score를 보내지 않습니다 (Node 4가 추정).")

    def score(self, bgr, faces=None) -> tuple[int | None, list]:
        """(사진 점수, 얼굴 상자별 점수 목록). faces는 FaceDetector 결과(원본 픽셀 좌표)."""
        faces = list(faces or [])
        with self._lock:
            if self._mesh is not None or self._tasks is not None:
                return self._score_mesh(bgr, faces)
            if self._haar is not None:
                return self._score_haar(bgr, faces)
        return None, [None] * len(faces)

    def _score_mesh(self, bgr, faces):
        import cv2
        h, w = bgr.shape[:2]
        scale = min(1.0, self.max_width / w)
        small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale < 1 else bgr
        sh, sw = small.shape[:2]
        scores, centers = [], []
        for marks in self._landmarks(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)):
            lm = [(p.x * sw / scale, p.y * sh / scale) for p in marks]
            scores.append(score_from_landmarks(lm))
            xs, ys = [p[0] for p in lm], [p[1] for p in lm]
            centers.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        if not faces:
            return photo_score(scores), []
        per_face = [None if i is None else scores[i] for i in match_to_faces(centers, faces)]
        # 얼굴 상자와 안 맞아도 Face Mesh가 찾은 얼굴은 사진 점수에 넣는다
        return photo_score(scores) if scores else None, per_face

    def _landmarks(self, rgb):
        """얼굴마다 정규화 좌표(0~1) 랜드마크 목록."""
        if self._mesh is not None:
            result = self._mesh.process(rgb)
            return [m.landmark for m in result.multi_face_landmarks or []]
        import numpy as np
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        return list(self._tasks.detect(image).face_landmarks or [])

    def _score_haar(self, bgr, faces):
        import cv2
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        per_face = []
        for f in faces:
            x0, x1 = max(0, f["x"]), min(gray.shape[1], f["x"] + f["w"])
            y0, y1 = max(0, f["y"] + int(f["h"] * 0.45)), min(gray.shape[0], f["y"] + f["h"])
            if x1 <= x0 or y1 <= y0:
                per_face.append(None)
                continue
            found = self._haar.detectMultiScale(gray[y0:y1, x0:x1], scaleFactor=1.7,
                                                minNeighbors=12, minSize=(16, 16))
            per_face.append(score_from_haar_count(len(found)))
        return photo_score(per_face), per_face

    def close(self):
        if self._mesh is not None:
            self._mesh.close()
        if self._tasks is not None:
            self._tasks.close()


def _main() -> None:
    import argparse

    import cv2

    from vision.faces import FaceDetector

    parser = argparse.ArgumentParser(description="사진별 미소 지수 확인 (상수 조정용)")
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    scorer = SmileScorer()
    detector = FaceDetector()
    print(f"[smile] {scorer.name} 사용")
    for name in args.images:
        image = cv2.imread(name)
        if image is None:
            print(f"{name}: 읽을 수 없음")
            continue
        faces = detector.detect(image, max_width=640)
        total, per_face = scorer.score(image, faces)
        print(f"{name}: smile_score={total} 얼굴 {len(faces)}명 {per_face}")
        if scorer.name.startswith("facemesh"):
            h, w = image.shape[:2]
            for marks in scorer._landmarks(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)):
                lm = [(p.x * w, p.y * h) for p in marks]
                width, lift = mouth_features(lm)
                print(f"    입 너비 {width:.3f}  입꼬리 {lift:+.3f}  → {score_from_landmarks(lm)}")


if __name__ == "__main__":
    _main()
