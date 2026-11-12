"""제스처 분류와 안정화 로직 테스트 (카메라·MediaPipe·OpenCV 없이 돈다)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.gestures import (  # noqa: E402
    FINGERS, PALM, THUMBS_UP, V_SIGN,
    GestureStabilizer, classify, shape_from_count, shape_from_landmarks,
)

# 손바닥 폭을 1로 본 손 모형. y는 손목에서 손끝 방향이 +.
_FINGER_X = (-0.45, -0.15, 0.15, 0.45)   # 검지·중지·약지·새끼 MCP의 x
_MCP_Y = (1.0, 1.05, 1.0, 0.85)


def make_hand(index=False, middle=False, ring=False, pinky=False, thumb=False,
              rotate_thumb_up=False, scale=100.0):
    """손가락별 펴짐 여부로 랜드마크 21개(픽셀, y 아래 방향)를 만든다."""
    lm = [(0.0, 0.0)] * 21
    lm[0] = (0.0, 0.0)
    lm[1] = (-0.4, 0.2)
    lm[2] = (-0.75, 0.45)
    if thumb:
        lm[3], lm[4] = (-1.05, 0.7), (-1.35, 0.95)
    else:
        lm[3], lm[4] = (-0.55, 0.7), (-0.15, 0.8)   # 손바닥 위로 접힘
    for i, extended in enumerate((index, middle, ring, pinky)):
        x, y = _FINGER_X[i], _MCP_Y[i]
        base = 5 + i * 4
        lm[base] = (x, y)
        if extended:
            lm[base + 1], lm[base + 2], lm[base + 3] = (x, y + 0.4), (x, y + 0.65), (x, y + 0.9)
        else:
            lm[base + 1], lm[base + 2], lm[base + 3] = (x, y + 0.3), (x, y + 0.1), (x, y - 0.2)
    points = []
    for x, y in lm:
        if rotate_thumb_up:      # 엄지가 위를 보도록 손을 90도 돌린다
            x, y = y, -x
        points.append((300 + x * scale, 300 - y * scale))
    return points


class ShapeTest(unittest.TestCase):
    def test_open_hand(self):
        shape = shape_from_landmarks(make_hand(True, True, True, True, thumb=True))
        self.assertEqual(shape.fingers, (True, True, True, True))
        self.assertTrue(shape.thumb)

    def test_fist(self):
        shape = shape_from_landmarks(make_hand())
        self.assertEqual(shape.count, 0)
        self.assertFalse(shape.thumb)

    def test_scale_does_not_matter(self):
        near = shape_from_landmarks(make_hand(True, True, scale=200))
        far = shape_from_landmarks(make_hand(True, True, scale=30))
        self.assertEqual(near, far)

    def test_thumb_up_needs_upward_thumb(self):
        up = shape_from_landmarks(make_hand(thumb=True, rotate_thumb_up=True))
        self.assertTrue(up.thumb_up)
        sideways = shape_from_landmarks(make_hand(thumb=True))
        self.assertFalse(sideways.thumb_up)


class ClassifyTest(unittest.TestCase):
    def shape(self, *args, **kwargs):
        return shape_from_landmarks(make_hand(*args, **kwargs))

    def test_idle_accepts_palm_and_v(self):
        self.assertEqual(classify(self.shape(True, True, True, True, thumb=True), "IDLE"), (PALM, None))
        self.assertEqual(classify(self.shape(True, True, True, True), "IDLE"), (PALM, None))
        self.assertEqual(classify(self.shape(True, True), "IDLE"), (V_SIGN, None))

    def test_idle_ignores_other_shapes(self):
        self.assertIsNone(classify(self.shape(True), "IDLE"))
        self.assertIsNone(classify(self.shape(thumb=True, rotate_thumb_up=True), "IDLE"))

    def test_select_counts_fingers(self):
        self.assertEqual(classify(self.shape(True), "SELECT"), (FINGERS, 1))
        self.assertEqual(classify(self.shape(True, True), "SELECT"), (FINGERS, 2))
        self.assertEqual(classify(self.shape(True, True, True), "SELECT"), (FINGERS, 3))
        self.assertEqual(classify(self.shape(True, True, True, True), "SELECT"), (FINGERS, 4))

    def test_select_thumbs_up_and_five(self):
        self.assertEqual(classify(self.shape(thumb=True, rotate_thumb_up=True), "SELECT"),
                         (THUMBS_UP, None))
        self.assertIsNone(classify(self.shape(True, True, True, True, thumb=True), "SELECT"))

    def test_states_without_gestures(self):
        palm = self.shape(True, True, True, True, thumb=True)
        for state in ("COUNTDOWN", "CAPTURE", "COMPOSE", "DONE", "ERROR"):
            self.assertIsNone(classify(palm, state))
        self.assertEqual(classify(palm, "SLEEP"), (PALM, None))

    def test_unknown_state_accepts_all(self):
        self.assertEqual(classify(self.shape(True, True, True, True, thumb=True), None), (PALM, None))
        self.assertEqual(classify(self.shape(True, True), None), (V_SIGN, None))
        self.assertEqual(classify(self.shape(True, True, True), None), (FINGERS, 3))

    def test_count_only_backend(self):
        self.assertEqual(classify(shape_from_count(5), "IDLE"), (PALM, None))
        self.assertEqual(classify(shape_from_count(2), "IDLE"), (V_SIGN, None))
        self.assertEqual(classify(shape_from_count(3), "SELECT"), (FINGERS, 3))


class StabilizerTest(unittest.TestCase):
    def feed(self, stabilizer, gesture, start, seconds, fps=15):
        out = []
        for i in range(int(seconds * fps)):
            result = stabilizer.update(gesture, start + i / fps)
            if result:
                out.append(result)
        return out

    def test_emits_once_after_hold(self):
        s = GestureStabilizer(hold_s=0.5)
        self.assertEqual(self.feed(s, (PALM, None), 0.0, 0.4), [])
        self.assertEqual(self.feed(s, (PALM, None), 0.4, 2.0), [(PALM, None)])

    def test_short_dropout_is_ignored(self):
        s = GestureStabilizer(hold_s=0.5, gap_s=0.15)
        self.feed(s, (V_SIGN, None), 0.0, 0.3)
        self.assertIsNone(s.update(None, 0.33))
        self.assertEqual(self.feed(s, (V_SIGN, None), 0.4, 0.5), [(V_SIGN, None)])

    def test_repeats_after_release(self):
        s = GestureStabilizer(hold_s=0.5, release_s=0.4)
        self.assertEqual(len(self.feed(s, (PALM, None), 0.0, 1.0)), 1)
        self.feed(s, None, 1.0, 1.0)
        self.assertEqual(len(self.feed(s, (PALM, None), 2.0, 1.0)), 1)

    def test_finger_value_change_is_new_gesture(self):
        s = GestureStabilizer(hold_s=0.5)
        self.assertEqual(self.feed(s, (FINGERS, 2), 0.0, 1.0), [(FINGERS, 2)])
        self.assertEqual(self.feed(s, (FINGERS, 3), 1.0, 1.0), [(FINGERS, 3)])


if __name__ == "__main__":
    unittest.main()
