"""시선 판단·업로드 재시도 테스트 (카메라·MediaPipe 없이 돈다)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision import gaze as g  # noqa: E402
from vision.facemesh import crop_box  # noqa: E402

try:
    from vision.camera import upload_with_retry
except ImportError:          # cv2 가 없는 PC
    upload_with_retry = None


def face(yaw=0.5, iris=0.5, open_=0.3, x=100.0, y=100.0, eye=100.0, iris_count=True):
    """가짜 Face Mesh 랜드마크. 두 눈 바깥 끝 거리 = eye 픽셀.

    yaw: 코끝 위치(0~1), iris: 두 눈동자의 눈 안 위치(0~1), open_: 눈 뜬 정도.
    """
    lm = [(0.0, 0.0)] * (478 if iris_count else 468)
    lm = list(lm)
    eye_w = eye * 0.3                       # 눈 하나 너비
    a_out, a_in = (x, y), (x + eye_w, y)
    b_in, b_out = (x + eye - eye_w, y), (x + eye, y)
    lm[g.EYE_A_OUT], lm[g.EYE_A_IN] = a_out, a_in
    lm[g.EYE_B_IN], lm[g.EYE_B_OUT] = b_in, b_out
    lm[g.EYE_A_TOP] = (x + eye_w / 2, y - eye_w * open_ / 2)
    lm[g.EYE_A_BOTTOM] = (x + eye_w / 2, y + eye_w * open_ / 2)
    lm[g.EYE_B_TOP] = (x + eye - eye_w / 2, y - eye_w * open_ / 2)
    lm[g.EYE_B_BOTTOM] = (x + eye - eye_w / 2, y + eye_w * open_ / 2)
    lm[g.NOSE_TIP] = (x + eye * yaw, y + eye * 0.5)
    if iris_count:
        for i in g.IRIS_A:
            lm[i] = (x + eye_w * iris, y)
        for i in g.IRIS_B:
            lm[i] = (x + eye - eye_w + eye_w * iris, y)
    return lm


class GazeFeatureTest(unittest.TestCase):
    def test_front_face_is_looking(self):
        f = g.gaze_features(face())
        self.assertAlmostEqual(f["yaw"], 0.5)
        self.assertAlmostEqual(f["iris"], 0.5)
        self.assertAlmostEqual(f["open"], 0.3)
        self.assertTrue(g.is_looking(f))
        self.assertEqual(g.reason(f), "looking")

    def test_head_turned(self):
        f = g.gaze_features(face(yaw=0.75))
        self.assertFalse(g.is_looking(f))
        self.assertEqual(g.reason(f), "head turned")

    def test_eyes_away(self):
        f = g.gaze_features(face(iris=0.8))
        self.assertFalse(g.is_looking(f))
        self.assertEqual(g.reason(f), "eyes away")

    def test_eyes_closed(self):
        f = g.gaze_features(face(open_=0.04))
        self.assertFalse(g.is_looking(f))
        self.assertEqual(g.reason(f), "eyes closed")

    def test_small_turn_is_still_ok(self):
        self.assertTrue(g.is_looking(g.gaze_features(face(yaw=0.58, iris=0.58))))

    def test_size_does_not_matter(self):
        near = g.gaze_features(face(eye=200, iris=0.6))
        far = g.gaze_features(face(eye=30, iris=0.6))
        self.assertAlmostEqual(near["iris"], far["iris"])

    def test_no_iris_landmarks(self):
        self.assertIsNone(g.gaze_features(face(iris_count=False)))
        self.assertIsNone(g.is_looking(None))
        self.assertEqual(g.reason(None), "?")


class PhotoGazeTest(unittest.TestCase):
    def test_everyone_must_look(self):
        self.assertTrue(g.photo_gaze([True, True]))
        self.assertFalse(g.photo_gaze([True, False]))

    def test_unknown_faces_are_ignored(self):
        self.assertTrue(g.photo_gaze([True, None]))
        self.assertIsNone(g.photo_gaze([None, None]))
        self.assertIsNone(g.photo_gaze([]))


class GazeVoteTest(unittest.TestCase):
    def test_single_blink_does_not_flip(self):
        vote = g.GazeVote(window_s=0.6)
        for t in (0.0, 0.15, 0.3):
            vote.update(True, t)
        self.assertTrue(vote.update(False, 0.45))      # 깜빡임 1번: 아직 True

    def test_looking_away_flips_after_window(self):
        vote = g.GazeVote(window_s=0.6)
        vote.update(True, 0.0)
        for t in (0.2, 0.4, 0.6, 0.8):
            result = vote.update(False, t)
        self.assertFalse(result)

    def test_old_results_expire(self):
        vote = g.GazeVote(window_s=0.6)
        vote.update(False, 0.0)
        self.assertIsNone(vote.update(None, 1.0))
        vote.reset()
        self.assertIsNone(vote.update(None, 1.1))


class CropTest(unittest.TestCase):
    def test_square_with_margin(self):
        x0, y0, x1, y1 = crop_box({"x": 100, "y": 100, "w": 40, "h": 50}, 1280, 720, margin=0.5)
        self.assertEqual(x1 - x0, y1 - y0)
        self.assertEqual(x1 - x0, 100)

    def test_clipped_at_edges(self):
        self.assertEqual(crop_box({"x": 0, "y": 0, "w": 40, "h": 40}, 1280, 720)[:2], (0, 0))


@unittest.skipIf(upload_with_retry is None, "OpenCV 없음")
class UploadRetryTest(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls, waits = [], []

        def flaky(url, path, fields, timeout):
            calls.append(timeout)
            if len(calls) < 3:
                raise ConnectionError("no route")

        ok, err = upload_with_retry("http://n4", Path("x.jpg"), {}, upload=flaky, sleep=waits.append)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(len(calls), 3)
        self.assertEqual(waits, [0.5, 1.0])

    def test_gives_up_with_error(self):
        def down(url, path, fields, timeout):
            raise ConnectionError("refused")

        ok, err = upload_with_retry("http://n4", Path("x.jpg"), {}, upload=down, sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertIn("refused", err)

    def test_worst_case_fits_capture_timeout(self):
        from vision import camera
        worst = camera.UPLOAD_ATTEMPTS * camera.UPLOAD_TIMEOUT_S + sum(camera.UPLOAD_BACKOFF_S)
        self.assertLess(worst, 10.0)      # Node 1 촬영 완료 대기 10초


if __name__ == "__main__":
    unittest.main()
