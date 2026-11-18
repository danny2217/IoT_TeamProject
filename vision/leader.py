"""여러 명이 있을 때 누구의 손을 제스처로 받을지 정한다 (대표자 손).

규칙
1. 대표자가 없으면 화면에서 가장 크게 보이는 손(카메라에 가장 가까운 손)이 후보다.
2. 후보의 제스처가 처음 확정되면(보통 대기 화면의 손바닥) 그 손을 대표자로 고정한다.
3. 고정된 뒤에는 대표자 손 위치 근처의 손만 받는다. 다른 사람의 손은 무시한다.
4. 대표자 손이 lost_s 동안 안 보이면 고정을 풀고 1번으로 돌아간다.
5. 세션이 끝나거나(세션 ID가 바뀜) 절전으로 가면 고정을 푼다.

카메라나 MediaPipe 없이 테스트할 수 있도록 순수 계산만 둔다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from vision.gestures import HandShape

NONE = "none"          # 대표자 없음 (가장 큰 손이 후보)
LOCKED = "locked"      # 대표자 손을 따라가는 중
MISSING = "missing"    # 대표자 손이 잠깐 안 보임 (다른 손은 계속 무시)


@dataclass(frozen=True)
class Hand:
    """한 손의 인식 결과. box는 인식에 쓴 영상 기준 픽셀 (x, y, w, h)."""

    shape: HandShape
    score: float
    box: tuple[int, int, int, int]
    side: str | None = None    # MediaPipe가 판단한 왼손/오른손 ("Left"/"Right"), 모르면 None

    @property
    def area(self) -> int:
        return self.box[2] * self.box[3]

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.box
        return x + w / 2, y + h / 2

    @property
    def size(self) -> float:
        return float(max(self.box[2], self.box[3]))


def box_from_points(points, pad: float = 0.1) -> tuple[int, int, int, int]:
    """랜드마크 좌표들을 감싸는 상자. 손가락 끝이 잘리지 않게 조금 넓힌다."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    px, py = w * pad, h * pad
    return (int(min(xs) - px), int(min(ys) - py), int(w + 2 * px), int(h + 2 * py))


def box_iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def dedupe_hands(hands, iou: float = 0.5):
    """같은 손을 두 번 잡은 결과를 하나로 합친다 (신뢰도 높은 쪽을 남김).

    MediaPipe Hands는 max_num_hands > 1 일 때 한 손에 상자 두 개를 내는 경우가 있다.
    """
    kept = []
    for hand in sorted(hands, key=lambda h: h.score, reverse=True):
        if all(box_iou(hand.box, k.box) < iou for k in kept):
            kept.append(hand)
    return kept


class LeaderTracker:
    """대표자 손 고정·추적.

    max_jump: 프레임 사이에 대표자 손이 움직일 수 있는 거리 (손 크기의 배수)
    stale_s:  이 시간 이상 대표자를 못 봤으면(촬영 중이라 인식을 쉬었을 때 등)
              stale_jump 배수까지 넓혀서 다시 찾는다. 사람은 대개 같은 자리에 서 있다.
    """

    def __init__(self, lost_s: float = 2.0, max_jump: float = 1.5,
                 stale_s: float = 1.0, stale_jump: float = 3.0):
        self.lost_s = lost_s
        self.max_jump = max_jump
        self.stale_s = stale_s
        self.stale_jump = stale_jump
        self.reset()

    def reset(self) -> None:
        self.leader: Hand | None = None
        self._last_seen = 0.0
        self._missing_since: float | None = None

    @property
    def status(self) -> str:
        if self.leader is None:
            return NONE
        return LOCKED if self._missing_since is None else MISSING

    def pick(self, hands, now: float) -> Hand | None:
        """이번 프레임에서 제스처를 읽을 손. 없으면 None."""
        if self.leader is None:
            return max(hands, key=lambda h: h.area) if hands else None

        # 한 사람의 두 손은 붙어 있어서 위치만으로는 대표자 손이 옆 손으로 넘어갈 수 있다.
        # 왼손/오른손을 알면 같은 쪽 손만 대표자 후보로 본다.
        if self.leader.side is not None:
            hands = [h for h in hands if h.side in (None, self.leader.side)]
        jump = self.stale_jump if now - self._last_seen >= self.stale_s else self.max_jump
        limit = jump * self.leader.size
        lx, ly = self.leader.center
        best, best_d = None, None
        for hand in hands:
            d = math.dist(hand.center, (lx, ly))
            if d <= limit and (best_d is None or d < best_d):
                best, best_d = hand, d
        if best is not None:
            self.leader = best
            self._last_seen = now
            self._missing_since = None
            return best

        if self._missing_since is None:
            self._missing_since = now
        elif now - self._missing_since >= self.lost_s:
            self.reset()
        return None

    def lock(self, hand: Hand, now: float) -> None:
        """후보 손의 제스처가 확정됐을 때 그 손을 대표자로 고정한다."""
        self.leader = hand
        self._last_seen = now
        self._missing_since = None

    def others(self, hands, chosen: Hand | None):
        """미리보기에서 회색으로 그릴 나머지 손들."""
        return [h for h in hands if h is not chosen]
