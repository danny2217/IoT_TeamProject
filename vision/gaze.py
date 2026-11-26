"""시선 판단 — 카메라를 보고 있는지 (제안서: 시선 셔터).

Face Mesh 랜드마크(refine=True, 눈동자 468~477 포함)로 얼굴마다 세 가지를 본다.
1. 고개 좌우(yaw): 코끝이 두 눈 바깥 끝 사이 어디에 있는지. 정면이면 0.5
2. 눈동자 위치: 눈동자 중심이 눈 양 끝 사이 어디에 있는지. 정면이면 0.5
3. 눈 뜬 정도: 눈꺼풀 사이 / 눈 너비. 감으면 0에 가깝다 (깜빡임)

세 가지가 모두 기준 안이면 그 사람은 "카메라를 봄". 화면 속 모두가 보면 gaze_ok.
Node 1 세션 관리는 3·2·1이 끝날 때 gaze_ok가 False면 최대 5초 "렌즈를 봐 주세요"를 띄우고 기다린다.

기준값(*_MAX, OPEN_MIN)은 실사진으로 맞춘다:
    python -m vision.gaze 정면.jpg 옆보기.jpg 눈감기.jpg
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

# Face Mesh 랜드마크 번호
NOSE_TIP = 1
EYE_A_OUT, EYE_A_IN, EYE_A_TOP, EYE_A_BOTTOM = 33, 133, 159, 145      # 화면 왼쪽 눈(본인 오른눈)
EYE_B_IN, EYE_B_OUT, EYE_B_TOP, EYE_B_BOTTOM = 362, 263, 386, 374     # 화면 오른쪽 눈
IRIS_A = range(468, 473)
IRIS_B = range(473, 478)

# 실기에서 조정할 값
YAW_MAX = 0.14      # |고개 위치 - 0.5|  (0.14 ≈ 고개를 20° 정도 돌림)
IRIS_MAX = 0.14     # |눈동자 위치 - 0.5| 두 눈 평균
OPEN_MIN = 0.12     # 눈 뜬 정도 (감으면 ~0.05, 뜨면 0.2~0.35)
VOTE_WINDOW_S = 0.6  # 이 시간 동안의 판단을 모아 다수결 (깜빡임 한 번에 경고가 뜨지 않게)


def _along(p, a, b) -> float:
    """점 p를 a→b 선분에 투영한 위치 (a=0, b=1)."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy or 1.0
    return ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2


def _center(lm, idx) -> tuple[float, float]:
    pts = [lm[i] for i in idx]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def gaze_features(lm) -> dict | None:
    """얼굴 하나의 (yaw, iris, open). 눈동자 랜드마크가 없으면(468개) None."""
    if lm is None or len(lm) < 478:
        return None
    left_x = min(lm[EYE_A_OUT][0], lm[EYE_B_OUT][0])
    # 화면에서 왼쪽에 있는 눈 바깥 끝 → 오른쪽 눈 바깥 끝 방향으로 잰다
    a_out, b_out = (lm[EYE_A_OUT], lm[EYE_B_OUT]) if lm[EYE_A_OUT][0] == left_x else (lm[EYE_B_OUT], lm[EYE_A_OUT])
    yaw = _along(lm[NOSE_TIP], a_out, b_out)

    eyes = ((EYE_A_OUT, EYE_A_IN, EYE_A_TOP, EYE_A_BOTTOM), (EYE_B_IN, EYE_B_OUT, EYE_B_TOP, EYE_B_BOTTOM))
    irises = (_center(lm, IRIS_A), _center(lm, IRIS_B))
    iris_pos, openness = [], []
    for (c1, c2, top, bottom) in eyes:
        p1, p2 = sorted((lm[c1], lm[c2]))               # x가 작은 쪽부터 (화면 왼→오)
        width = math.dist(p1, p2) or 1.0
        mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        iris = min(irises, key=lambda c: math.dist(c, mid))   # 이 눈에 가까운 눈동자
        iris_pos.append(_along(iris, p1, p2))
        openness.append(math.dist(lm[top], lm[bottom]) / width)
    return {"yaw": yaw, "iris": sum(iris_pos) / 2,
            "iris_off": sum(abs(v - 0.5) for v in iris_pos) / 2, "open": sum(openness) / 2}


def is_looking(features: dict | None) -> bool | None:
    """카메라를 보면 True, 아니면 False, 판단 불가(랜드마크 없음)면 None."""
    if features is None:
        return None
    return (abs(features["yaw"] - 0.5) <= YAW_MAX
            and features["iris_off"] <= IRIS_MAX
            and features["open"] >= OPEN_MIN)


def reason(features: dict | None) -> str:
    """미리보기·로그용 짧은 이유."""
    if features is None:
        return "?"
    if features["open"] < OPEN_MIN:
        return "eyes closed"
    if abs(features["yaw"] - 0.5) > YAW_MAX:
        return "head turned"
    if features["iris_off"] > IRIS_MAX:
        return "eyes away"
    return "looking"


def photo_gaze(per_face_looking) -> bool | None:
    """얼굴별 판단 → 사진 판단. 판단 가능한 얼굴이 모두 보면 True, 판단 가능한 얼굴이 없으면 None."""
    known = [v for v in per_face_looking if v is not None]
    return all(known) if known else None


class GazeVote:
    """최근 VOTE_WINDOW_S 동안의 사진 판단을 다수결. 깜빡임처럼 짧은 False는 무시된다."""

    def __init__(self, window_s: float = VOTE_WINDOW_S):
        self.window_s = window_s
        self._items: deque[tuple[float, bool]] = deque()

    def reset(self) -> None:
        self._items.clear()

    def update(self, value: bool | None, now: float) -> bool | None:
        if value is not None:
            self._items.append((now, value))
        while self._items and now - self._items[0][0] > self.window_s:
            self._items.popleft()
        if not self._items:
            return None
        yes = sum(1 for _, v in self._items if v)
        return yes * 2 >= len(self._items)


class GazeMonitor:
    """카운트다운 동안 백그라운드 스레드에서 최신 프레임의 시선을 계속 판단한다.

    메인 루프는 submit(frame, faces)만 부르고 바로 돌아간다 (손·화면 처리 속도를 안 떨어뜨림).
    result()는 (gaze_ok 다수결, 얼굴별 [(box, looking, 이유)]) 를 돌려준다.
    """

    def __init__(self, mesh=None, interval_s: float = 0.15):
        if mesh is None:
            from vision.facemesh import FaceMesh
            mesh = FaceMesh(refine=True)
        self.mesh = mesh
        self.interval_s = interval_s
        self.vote = GazeVote()
        self._lock = threading.Lock()
        self._job = None
        self._ok = None
        self._detail: list = []
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def submit(self, frame, faces: list[dict]) -> None:
        with self._lock:
            self._job = (frame, list(faces))
        self._wake.set()

    def reset(self) -> None:
        with self._lock:
            self._job = None
            self._ok = None
            self._detail = []
            self.vote.reset()

    def result(self) -> tuple[bool | None, list]:
        with self._lock:
            return self._ok, list(self._detail)

    def _loop(self) -> None:
        while not self._stop:
            self._wake.wait(1.0)
            self._wake.clear()
            with self._lock:
                job, self._job = self._job, None
            if job is None:
                continue
            frame, faces = job
            started = time.monotonic()
            try:
                marks = self.mesh.for_faces(frame, faces) if faces else []
            except Exception as exc:  # 한 번 실패해도 계속
                print(f"[gaze] 판단 실패: {exc}")
                continue
            feats = [gaze_features(lm) for lm in marks]
            looks = [is_looking(f) for f in feats]
            now = time.monotonic()
            with self._lock:
                self._ok = self.vote.update(photo_gaze(looks), now)
                self._detail = [(f, lk, reason(ft)) for f, lk, ft in zip(faces, looks, feats)]
            spare = self.interval_s - (now - started)
            if spare > 0:
                time.sleep(spare)

    def close(self) -> None:
        self._stop = True
        self._wake.set()


def _main() -> None:
    import argparse

    import cv2

    from vision.facemesh import FaceMesh
    from vision.faces import FaceDetector

    parser = argparse.ArgumentParser(description="사진별 시선 판단 확인 (기준값 조정용)")
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    mesh = FaceMesh(refine=True)
    detector = FaceDetector()
    for name in args.images:
        image = cv2.imread(name)
        if image is None:
            print(f"{name}: 읽을 수 없음")
            continue
        faces = detector.detect(image, max_width=640)
        print(f"{name}: 얼굴 {len(faces)}명")
        for lm in mesh.for_faces(image, faces):
            f = gaze_features(lm)
            if f is None:
                print("    (Face Mesh 못 찾음)")
                continue
            print(f"    고개 {f['yaw']:.3f}  눈동자 {f['iris']:.3f} (치우침 {f['iris_off']:.3f})  "
                  f"눈 뜸 {f['open']:.3f}  → {reason(f)}")


if __name__ == "__main__":
    _main()
