"""손 모양 → 규약 v1 제스처(PALM · V_SIGN · FINGERS · THUMBS_UP).

카메라나 MediaPipe 없이 테스트할 수 있도록 순수 계산만 둔다.
입력은 MediaPipe Hands 순서의 랜드마크 21개 (x, y) 픽셀 좌표다(y는 아래로 증가).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PALM = "PALM"
V_SIGN = "V_SIGN"
FINGERS = "FINGERS"
THUMBS_UP = "THUMBS_UP"

# 세션 상태별로 받아들일 제스처. V 사인과 손가락 2개, 손바닥과 손가락 4개는
# 모양이 같아서 상태로 구분한다. None은 세션 상태를 모를 때(단독 테스트)다.
ALLOWED = {
    None: {PALM, V_SIGN, FINGERS, THUMBS_UP},
    "SLEEP": {PALM},
    "IDLE": {PALM, V_SIGN},
    "SELECT": {FINGERS, THUMBS_UP},
}

# 실기에서 조정할 임계값
FINGER_RATIO = 1.2   # 손끝이 PIP 관절보다 손목에서 이만큼 멀면 편 손가락
THUMB_RATIO = 1.1    # 엄지 끝이 IP 관절보다 새끼 MCP에서 이만큼 멀면 편 엄지

_WRIST = 0
_THUMB_MCP, _THUMB_IP, _THUMB_TIP = 2, 3, 4
_PINKY_MCP = 17
_FINGERS = ((6, 8), (10, 12), (14, 16), (18, 20))  # (PIP, TIP): 검지·중지·약지·새끼
_KNUCKLES = (5, 9, 13, 17)


@dataclass(frozen=True)
class HandShape:
    """손 인식 방식(MediaPipe / OpenCV)과 무관한 손 모양 요약."""

    fingers: tuple[bool, bool, bool, bool]  # 검지·중지·약지·새끼가 펴졌는지
    thumb: bool = False
    thumb_up: bool = False                  # 엄지가 위를 향하는지

    @property
    def count(self) -> int:
        return sum(self.fingers)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def shape_from_landmarks(lm) -> HandShape:
    """랜드마크 21개로 손가락별 펴짐 여부를 구한다. 손 회전에 영향받지 않게 거리 비율을 쓴다."""
    if len(lm) != 21:
        raise ValueError("랜드마크는 21개여야 합니다.")
    wrist = lm[_WRIST]
    fingers = tuple(_dist(lm[tip], wrist) > _dist(lm[pip], wrist) * FINGER_RATIO
                    for pip, tip in _FINGERS)
    thumb = (_dist(lm[_THUMB_TIP], lm[_PINKY_MCP])
             > _dist(lm[_THUMB_IP], lm[_PINKY_MCP]) * THUMB_RATIO)
    top_knuckle = min(lm[i][1] for i in _KNUCKLES)
    thumb_up = (thumb and lm[_THUMB_TIP][1] < lm[_THUMB_MCP][1]
                and lm[_THUMB_TIP][1] < top_knuckle)
    return HandShape(fingers=fingers, thumb=thumb, thumb_up=thumb_up)


def shape_from_count(count: int) -> HandShape:
    """OpenCV 대체 방식처럼 편 손가락 개수(0~5)만 알 때. 엄지 올리기는 구분하지 못한다."""
    count = max(0, min(5, int(count)))
    n = min(count, 4)
    return HandShape(fingers=tuple(i < n for i in range(4)), thumb=count == 5)


def classify(shape: HandShape, state: str | None = None):
    """손 모양과 세션 상태로 (gesture, value)를 정한다. 해당 없으면 None."""
    allowed = ALLOWED.get(state, set())
    if not allowed:
        return None
    n = shape.count

    if THUMBS_UP in allowed and n == 0 and shape.thumb_up:
        return THUMBS_UP, None
    if PALM in allowed and n == 4 and (shape.thumb or FINGERS not in allowed):
        return PALM, None
    if V_SIGN in allowed and shape.fingers == (True, True, False, False):
        return V_SIGN, None
    if FINGERS in allowed and 1 <= n <= 4 and not (n == 4 and shape.thumb):
        return FINGERS, n
    return None


class GestureStabilizer:
    """같은 제스처가 일정 시간 유지될 때만 한 번 내보낸다.

    - hold_s 동안 유지되어야 인정한다.
    - gap_s 이하의 짧은 인식 끊김은 무시한다.
    - 한 번 내보낸 제스처는 손을 내리거나(release_s) 다른 제스처로 바꿔야 다시 나간다.
    """

    def __init__(self, hold_s: float = 0.5, gap_s: float = 0.15, release_s: float = 0.4):
        self.hold_s = hold_s
        self.gap_s = gap_s
        self.release_s = release_s
        self._candidate = None
        self._since = 0.0
        self._last_seen = 0.0
        self._emitted = None
        self._none_since = None

    def reset(self) -> None:
        self._candidate = None
        self._emitted = None
        self._none_since = None

    def update(self, gesture, now: float):
        """프레임마다 호출한다. 새로 확정된 제스처가 있으면 돌려주고, 없으면 None."""
        if gesture is None:
            if self._none_since is None:
                self._none_since = now
            elif now - self._none_since >= self.release_s:
                self._emitted = None
        else:
            self._none_since = None

        if gesture == self._candidate:
            self._last_seen = now
        elif self._candidate is None or now - self._last_seen > self.gap_s:
            self._candidate = gesture
            self._since = now
            self._last_seen = now

        if (self._candidate is not None and self._candidate == gesture
                and now - self._since >= self.hold_s
                and self._candidate != self._emitted):
            self._emitted = self._candidate
            return self._candidate
        return None
