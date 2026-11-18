"""시계 차 계산과 예약 시각 프레임 고르기 테스트 (카메라 없이 돈다)."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.clockcheck import offset_of, summarize  # noqa: E402

try:
    from vision.camera import Camera
except ImportError:          # cv2 가 없는 PC
    Camera = None


class ClockTest(unittest.TestCase):
    def test_offset_formula(self):
        # 기준 0ms에 보냄, 노드 시계로 55ms에 답함, 기준 20ms에 받음 → 노드가 45ms 빠름
        self.assertEqual(offset_of(0, 55, 20), (45.0, 20.0))

    def test_summary_drops_slow_round_trips(self):
        samples = [(5.0, 10.0), (6.0, 12.0), (4.0, 11.0), (80.0, 300.0)]
        s = summarize(samples)
        self.assertEqual(s["used"], 2)
        self.assertEqual(s["offset_ms"], 4.5)
        self.assertEqual(s["error_ms"], 5.0)

    def test_empty(self):
        self.assertEqual(summarize([]), {})


@unittest.skipIf(Camera is None, "OpenCV 없음")
class FrameNearTest(unittest.TestCase):
    def make(self):
        cam = Camera.__new__(Camera)
        cam._init_buffer()
        return cam

    def test_picks_first_frame_after_target(self):
        cam = self.make()
        cam._recent.extend([(1000, "a"), (1033, "b"), (1066, "c")])
        self.assertEqual(cam.frame_near(1034, after=True), ("c", 1066))  # 1033이 더 가깝지만 직전
        self.assertEqual(cam.frame_near(1033, after=True), ("b", 1033))

    def test_closest_mode(self):
        cam = self.make()
        cam._recent.extend([(1000, "a"), (1033, "b"), (1066, "c")])
        self.assertEqual(cam.frame_near(1040), ("b", 1033))

    def test_waits_for_frame_after_target(self):
        cam = self.make()
        cam._recent.append((1000, "old"))
        target = int(time.time() * 1000) + 30

        def later():
            time.sleep(0.05)
            cam._store("new")

        threading.Thread(target=later).start()
        frame, _ = cam.frame_near(target, timeout_s=1.0, after=True)
        self.assertEqual(frame, "new")

    def test_no_frames(self):
        self.assertEqual(self.make().frame_near(0, timeout_s=0.01), (None, None))


if __name__ == "__main__":
    unittest.main()
