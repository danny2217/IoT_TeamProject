"""미소 지수 계산 테스트 (카메라·MediaPipe 없이 돈다)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.smile import (  # noqa: E402
    LIFT_HI, LIFT_LO, WIDTH_HI, WIDTH_LO, EYE_LEFT_OUT, EYE_RIGHT_OUT, LIP_LOWER, LIP_UPPER, MOUTH_LEFT, MOUTH_RIGHT,
    match_to_faces, mouth_features, photo_score, score_from_haar_count, score_from_landmarks,
)


def face(mouth_width=WIDTH_LO, lift=0.0, scale=100.0, ox=300.0, oy=300.0):
    """두 눈 바깥 끝 거리를 1로 본 얼굴 랜드마크 468개 (y 아래로 증가)."""
    lm = [(ox, oy)] * 468

    def put(i, x, y):
        lm[i] = (ox + x * scale, oy + y * scale)

    put(EYE_LEFT_OUT, -0.5, 0.0)
    put(EYE_RIGHT_OUT, 0.5, 0.0)
    put(LIP_UPPER, 0.0, 0.95)
    put(LIP_LOWER, 0.0, 1.05)
    put(MOUTH_LEFT, -mouth_width / 2, 1.0 - lift)
    put(MOUTH_RIGHT, mouth_width / 2, 1.0 - lift)
    return lm


class FeatureTest(unittest.TestCase):
    def test_features(self):
        width, lift = mouth_features(face(mouth_width=0.6, lift=0.03))
        self.assertAlmostEqual(width, 0.6, places=3)
        self.assertAlmostEqual(lift, 0.03, places=3)

    def test_neutral_is_low_and_big_smile_is_high(self):
        self.assertEqual(score_from_landmarks(face(WIDTH_LO, LIFT_LO)), 0)
        self.assertEqual(score_from_landmarks(face(WIDTH_HI, LIFT_HI)), 100)

    def test_wider_and_higher_corners_score_more(self):
        neutral = score_from_landmarks(face(0.51, 0.0))
        slight = score_from_landmarks(face(0.58, 0.025))
        big = score_from_landmarks(face(0.66, 0.05))
        self.assertLess(neutral, slight)
        self.assertLess(slight, big)

    def test_frown_is_clamped_to_zero(self):
        self.assertEqual(score_from_landmarks(face(0.45, -0.05)), 0)

    def test_distance_from_camera_does_not_matter(self):
        near = score_from_landmarks(face(0.6, 0.03, scale=200))
        far = score_from_landmarks(face(0.6, 0.03, scale=40))
        self.assertEqual(near, far)


class PhotoTest(unittest.TestCase):
    def test_photo_score_is_mean_of_faces(self):
        self.assertEqual(photo_score([80, 40, None]), 60)
        self.assertIsNone(photo_score([]))
        self.assertIsNone(photo_score([None]))

    def test_haar_table_matches_node4(self):
        self.assertEqual(score_from_haar_count(0), 32.0)
        self.assertEqual(score_from_haar_count(1), 72.0)
        self.assertEqual(score_from_haar_count(5), 100.0)

    def test_match_mesh_faces_to_boxes(self):
        faces = [{"x": 0, "y": 0, "w": 100, "h": 100}, {"x": 200, "y": 0, "w": 100, "h": 100},
                 {"x": 500, "y": 0, "w": 50, "h": 50}]
        self.assertEqual(match_to_faces([(250, 50), (50, 40)], faces), [1, 0, None])


if __name__ == "__main__":
    unittest.main()
