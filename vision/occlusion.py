"""손에 가려진 얼굴도 인원수에 넣는다.

V 사인을 얼굴 앞에서 하면 얼굴 검출이 그 사람을 놓쳐 인원수가 줄고(3명→2명),
레이아웃이 2×2 → 세로 4컷으로 뒤집힐 수 있다.

규칙
- 직전에 얼굴이 있던 자리에 지금 손이 겹쳐 있으면 → 그 얼굴은 "가려진 것"으로 보고 계속 센다.
- 손도 없이 사라졌으면 → 실제로 나간 것으로 보고 뺀다.
- 가려진 채로 max_hold_s 가 지나면 뺀다 (손만 남고 사람이 나간 경우 대비).
- 손 인식도 한두 번 깜빡일 수 있으므로, 손 없이 사라진 얼굴도 grace_s 동안은 유지한다.
- 가려졌던 얼굴 근처에서 얼굴이 다시 보이면(조금 움직였어도) 같은 사람으로 보고 두 번 세지 않는다.

좌표는 모두 같은 영상 기준 픽셀 (x, y, w, h). 카메라·MediaPipe 없이 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _box(f) -> tuple[float, float, float, float]:
    if isinstance(f, dict):
        return f["x"], f["y"], f["w"], f["h"]
    return tuple(f)


def overlap_ratio(face, hand) -> float:
    """얼굴 상자 중 손 상자와 겹치는 비율 (0~1)."""
    fx, fy, fw, fh = _box(face)
    hx, hy, hw, hh = _box(hand)
    ix = max(0.0, min(fx + fw, hx + hw) - max(fx, hx))
    iy = max(0.0, min(fy + fh, hy + hh) - max(fy, hy))
    area = fw * fh
    return (ix * iy) / area if area > 0 else 0.0


def _dist(a, b) -> float:
    ax, ay, aw, ah = _box(a)
    bx, by, bw, bh = _box(b)
    return (((ax + aw / 2) - (bx + bw / 2)) ** 2 + ((ay + ah / 2) - (by + bh / 2)) ** 2) ** 0.5


def same_face(a, b, tol: float = 0.6) -> bool:
    """두 얼굴 상자의 중심이 얼굴 크기의 tol 배 안이면 같은 사람."""
    ax, ay, aw, ah = _box(a)
    bx, by, bw, bh = _box(b)
    dx = (ax + aw / 2) - (bx + bw / 2)
    dy = (ay + ah / 2) - (by + bh / 2)
    size = max(aw, ah, bw, bh)
    return (dx * dx + dy * dy) ** 0.5 <= tol * size


@dataclass
class _Remembered:
    box: tuple
    hidden_since: float | None = None


@dataclass
class OccludedFaceMemory:
    min_overlap: float = 0.25     # 얼굴의 25% 이상이 손에 덮이면 가려진 것
    max_hold_s: float = 5.0       # 가려진 얼굴을 이 시간까지만 유지
    grace_s: float = 1.0          # 손 없이 사라져도 이 시간은 유지 (얼굴·손 인식 깜빡임)
    rejoin_tol: float = 1.5       # 다시 보인 얼굴이 이 배수(얼굴 크기) 안이면 같은 사람
    _faces: list = field(default_factory=list)

    def reset(self) -> None:
        self._faces = []

    def update(self, faces, hands, now: float) -> tuple[int, list]:
        """(인원수, 가려진 것으로 본 얼굴 상자 목록). hands는 손 상자 목록."""
        hands = [_box(h) for h in hands]
        seen = [_box(f) for f in faces]
        kept: list[_Remembered] = [_Remembered(b) for b in seen]
        hidden = []
        # 지금 보이는 얼굴과 기억 속 얼굴을 가까운 쌍부터 1:1로 짝짓는다.
        # (바짝 붙은 두 사람 중 한 명이 가려졌을 때, 보이는 한 명이 둘 몫을 하지 않게)
        pairs = sorted((_dist(o.box, b), i, j) for i, o in enumerate(self._faces)
                       for j, b in enumerate(seen) if same_face(o.box, b, self.rejoin_tol))
        matched_old, used_now = set(), set()
        for _, i, j in pairs:
            if i not in matched_old and j not in used_now:
                matched_old.add(i)
                used_now.add(j)
        for i, old in enumerate(self._faces):
            if i in matched_old:
                continue                                   # 지금도 보임 (조금 움직였어도 같은 사람)
            since = old.hidden_since if old.hidden_since is not None else now
            covered = any(overlap_ratio(old.box, h) >= self.min_overlap for h in hands)
            if not covered and now - since > self.grace_s:
                continue                                   # 손 없이 사라진 지 오래 → 나감
            if now - since > self.max_hold_s:
                continue                                   # 너무 오래 가려짐 → 뺌
            kept.append(_Remembered(old.box, since))
            hidden.append(old.box)
        self._faces = kept
        return len(seen) + len(hidden), hidden

    @property
    def count(self) -> int:
        return len(self._faces)
