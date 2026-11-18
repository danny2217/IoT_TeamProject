"""손에 가려진 얼굴 인원수 유지 테스트 (카메라 없이 돈다)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.occlusion import OccludedFaceMemory, overlap_ratio, same_face  # noqa: E402

A = {"x": 20, "y": 40, "w": 40, "h": 40}
B = {"x": 140, "y": 40, "w": 40, "h": 40}
C = {"x": 250, "y": 40, "w": 40, "h": 40}
HAND_ON_B = (150, 50, 35, 40)      # B 얼굴 앞의 V 사인
HAND_AWAY = (150, 180, 35, 40)     # B 아래쪽 (가슴 앞)


class GeometryTest(unittest.TestCase):
    def test_overlap(self):
        self.assertAlmostEqual(overlap_ratio(B, HAND_ON_B), (30 * 30) / (40 * 40))
        self.assertEqual(overlap_ratio(B, HAND_AWAY), 0.0)

    def test_same_face_tolerates_small_move(self):
        self.assertTrue(same_face(B, {"x": 150, "y": 45, "w": 40, "h": 40}))
        self.assertFalse(same_face(A, B))


class MemoryTest(unittest.TestCase):
    def test_hand_over_face_keeps_count(self):
        m = OccludedFaceMemory()
        self.assertEqual(m.update([A, B, C], [], 0.0)[0], 3)
        count, hidden = m.update([A, C], [HAND_ON_B], 0.3)     # B가 V로 얼굴을 가림
        self.assertEqual(count, 3)
        self.assertEqual(hidden, [(140, 40, 40, 40)])

    def test_face_gone_without_hand_is_removed_after_grace(self):
        m = OccludedFaceMemory(grace_s=1.0)
        m.update([A, B, C], [], 0.0)
        self.assertEqual(m.update([A, C], [HAND_AWAY], 0.3)[0], 3)   # 깜빡임일 수 있어 유지
        self.assertEqual(m.update([A, C], [HAND_AWAY], 1.5)[0], 2)   # 1초 넘게 없으면 나감

    def test_hand_flicker_does_not_drop_hidden_face(self):
        m = OccludedFaceMemory(grace_s=1.0)
        m.update([A, B], [], 0.0)
        m.update([A], [HAND_ON_B], 0.3)
        self.assertEqual(m.update([A], [], 0.6)[0], 2)               # 손도 한 번 놓침
        self.assertEqual(m.update([A], [HAND_ON_B], 0.9)[0], 2)

    def test_moved_face_is_not_counted_twice(self):
        m = OccludedFaceMemory()
        m.update([A, B], [], 0.0)
        m.update([A], [HAND_ON_B], 0.3)
        moved_b = {"x": 175, "y": 50, "w": 40, "h": 40}             # 손 내리며 옆으로 35px
        self.assertEqual(m.update([A, moved_b], [], 0.6)[0], 2)

    def test_close_neighbor_does_not_absorb_hidden_face(self):
        m = OccludedFaceMemory()
        left = {"x": 100, "y": 40, "w": 40, "h": 40}
        right = {"x": 150, "y": 40, "w": 40, "h": 40}               # 얼굴 중심 50px = 1.25배
        m.update([left, right], [], 0.0)
        self.assertEqual(m.update([left], [(155, 45, 35, 40)], 0.3)[0], 2)

    def test_hidden_too_long_is_dropped(self):
        m = OccludedFaceMemory(max_hold_s=5.0)
        m.update([A, B], [], 0.0)
        for t in (0.3, 2.0, 4.0, 5.2):
            count, _ = m.update([A], [HAND_ON_B], t)
        self.assertEqual(count, 2)                              # 0.3초부터 4.9초째
        self.assertEqual(m.update([A], [HAND_ON_B], 5.4)[0], 1)  # 5초 넘음

    def test_face_comes_back(self):
        m = OccludedFaceMemory()
        m.update([A, B], [], 0.0)
        m.update([A], [HAND_ON_B], 0.3)
        self.assertEqual(m.update([A, B], [], 0.6), (2, []))

    def test_new_person_counts_immediately(self):
        m = OccludedFaceMemory()
        m.update([A], [], 0.0)
        self.assertEqual(m.update([A, B], [], 0.3)[0], 2)

    def test_reset(self):
        m = OccludedFaceMemory()
        m.update([A, B], [], 0.0)
        m.reset()
        self.assertEqual(m.update([A], [HAND_ON_B], 0.3)[0], 1)


if __name__ == "__main__":
    unittest.main()
