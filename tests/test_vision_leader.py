"""대표자 손 고정·추적 테스트 (카메라·MediaPipe 없이 돈다)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.gestures import PALM, V_SIGN, GestureStabilizer, classify, shape_from_count  # noqa: E402
from vision.leader import (  # noqa: E402
    LOCKED, MISSING, NONE, Hand, LeaderTracker, box_from_points, dedupe_hands)


def hand(x, y, size=40, fingers=5):
    return Hand(shape_from_count(fingers), 0.9, (x, y, size, size))


class PickTest(unittest.TestCase):
    def test_largest_hand_is_candidate_before_lock(self):
        t = LeaderTracker()
        small, big = hand(10, 10, 30), hand(200, 100, 60)
        self.assertIs(t.pick([small, big], 0.0), big)
        self.assertEqual(t.status, NONE)

    def test_no_hands(self):
        self.assertIsNone(LeaderTracker().pick([], 0.0))

    def test_locked_leader_ignores_bigger_stranger(self):
        t = LeaderTracker()
        t.lock(hand(100, 100, 40), 0.0)
        mine = hand(110, 105, 40)          # 대표자 손이 조금 움직임
        stranger = hand(250, 100, 80)      # 더 크지만 멀리 있는 다른 사람 손
        self.assertIs(t.pick([stranger, mine], 0.1), mine)
        self.assertEqual(t.status, LOCKED)

    def test_follows_leader_as_it_moves(self):
        t = LeaderTracker()
        t.lock(hand(100, 100), 0.0)
        for i in range(1, 10):             # 프레임마다 20px씩 이동
            picked = t.pick([hand(100 + 20 * i, 100), hand(20, 180)], i * 0.07)
            self.assertEqual(picked.box[0], 100 + 20 * i)

    def test_missing_then_released_after_lost_s(self):
        t = LeaderTracker(lost_s=2.0)
        t.lock(hand(100, 100), 0.0)
        stranger = hand(260, 60)
        self.assertIsNone(t.pick([stranger], 0.1))   # 다른 손은 무시
        self.assertEqual(t.status, MISSING)
        self.assertIsNone(t.pick([stranger], 1.5))
        t.pick([stranger], 2.2)                      # 2초 지나면 해제
        self.assertEqual(t.status, NONE)
        self.assertIs(t.pick([stranger], 2.3), stranger)

    def test_comes_back_before_lost_s(self):
        t = LeaderTracker(lost_s=2.0)
        t.lock(hand(100, 100), 0.0)
        t.pick([], 0.5)
        self.assertEqual(t.status, MISSING)
        back = hand(105, 100)
        self.assertIs(t.pick([back], 1.0), back)
        self.assertEqual(t.status, LOCKED)

    def test_stale_leader_found_in_wider_area(self):
        # 촬영 중엔 손 인식을 쉬어서, 프레임 선택 화면에서 손 위치가 꽤 달라져 있다
        t = LeaderTracker(max_jump=1.5, stale_s=1.0, stale_jump=3.0)
        t.lock(hand(100, 100, 40), 0.0)
        moved = hand(200, 100, 40)                   # 100px = 손 크기 2.5배
        self.assertIs(t.pick([moved], 5.0), moved)

    def test_reset_releases(self):
        t = LeaderTracker()
        t.lock(hand(100, 100), 0.0)
        t.reset()
        self.assertEqual(t.status, NONE)


class FlowTest(unittest.TestCase):
    """node2 루프와 같은 순서로: 후보 → 제스처 확정 시 고정 → 다른 사람 제스처 무시."""

    def test_stranger_v_sign_is_ignored_after_lock(self):
        t = LeaderTracker()
        s = GestureStabilizer(hold_s=0.5)
        leader_palm, other_small = hand(100, 100, 60, 5), hand(250, 100, 30, 0)
        confirmed = None
        for i in range(12):                          # 대표자(큰 손)가 손바닥 0.8초
            now = i / 15
            chosen = t.pick([leader_palm, other_small], now)
            got = s.update(classify(chosen.shape, "IDLE"), now)
            if got:
                confirmed = got
                t.lock(chosen, now)
        self.assertEqual(confirmed, (PALM, None))
        self.assertEqual(t.status, LOCKED)

        # 대표자는 주먹, 옆 사람이 더 크게 V 사인 → 무시
        fist, other_v = hand(100, 100, 60, 0), hand(250, 100, 90, 2)
        emitted = []
        for i in range(15):
            now = 1.0 + i / 15
            chosen = t.pick([fist, other_v], now)
            got = s.update(classify(chosen.shape, "IDLE") if chosen else None, now)
            if got:
                emitted.append(got)
        self.assertNotIn((V_SIGN, None), emitted)


class SideTest(unittest.TestCase):
    def test_leader_does_not_jump_to_own_other_hand(self):
        t = LeaderTracker()
        right = Hand(shape_from_count(5), 0.9, (100, 100, 40, 40), side="Right")
        t.lock(right, 0.0)
        left = Hand(shape_from_count(2), 0.9, (150, 100, 40, 40), side="Left")  # 바로 옆 왼손 V
        self.assertIsNone(t.pick([left], 0.1))          # 오른손을 한 번 놓쳐도 왼손을 잡지 않음
        right2 = Hand(shape_from_count(5), 0.9, (105, 100, 40, 40), side="Right")
        self.assertIs(t.pick([left, right2], 0.2), right2)

    def test_unknown_side_still_works(self):
        t = LeaderTracker()
        t.lock(hand(100, 100), 0.0)
        near = hand(110, 100)
        self.assertIs(t.pick([near], 0.1), near)


class DedupeTest(unittest.TestCase):
    def test_same_hand_twice_becomes_one(self):
        a = Hand(shape_from_count(5), 0.95, (100, 100, 60, 80))
        b = Hand(shape_from_count(5), 0.80, (104, 96, 62, 84))   # 같은 손, 조금 어긋난 상자
        c = Hand(shape_from_count(2), 0.90, (300, 100, 60, 80))  # 다른 손
        out = dedupe_hands([b, a, c])
        self.assertEqual(len(out), 2)
        self.assertIn(a, out)
        self.assertIn(c, out)


class BoxTest(unittest.TestCase):
    def test_box_covers_points_with_padding(self):
        x, y, w, h = box_from_points([(10, 20), (110, 220)], pad=0.1)
        self.assertEqual((x, y, w, h), (0, 0, 120, 240))


if __name__ == "__main__":
    unittest.main()
