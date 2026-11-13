"""Unit tests for node1_main/session_machine.py (no broker, no Arduino needed).

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "node1_main"))

from session_machine import (CAPTURE, COMPOSE, COUNTDOWN, DONE, ERROR, IDLE,  # noqa: E402
                             SELECT, SLEEP, Config, SessionMachine)


class FakeClock:
    def __init__(self, start=1_763_870_000.0):
        self.now = start

    def __call__(self):
        return self.now


class Harness:
    """Machine + fake clock + collected outputs."""

    def __init__(self, **cfg):
        self.clock = FakeClock()
        self.m = SessionMachine(Config(**cfg), clock=self.clock)
        self.out = []
        self.collect()

    def collect(self):
        new = self.m.drain()
        self.out.extend(new)
        return new

    def run(self, seconds, step=0.05):
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.clock.now += step
            self.m.tick()
        return self.collect()

    def send(self, **event):
        self.m.handle(event)
        return self.collect()

    def arduino(self):
        return [d for k, d in self.out if k == "arduino"]

    def states(self):
        return [d["state"] for k, d in self.out if k == "state"]

    def of(self, kind):
        return [d for k, d in self.out if k == kind]

    def cameras_report(self):
        sid, shot = self.m.session_id, self.m.shot
        for node in self.m.cfg.expected_cameras:
            self.send(type="capture_done", session_id=sid, shot=shot, node=node, face_count=2)


class HappyPath(unittest.TestCase):
    def test_full_session(self):
        h = Harness()
        self.assertEqual(h.m.state, SLEEP)

        h.send(type="proximity", event="WAKE_UP")
        self.assertEqual(h.m.state, IDLE)
        self.assertIsNone(h.m.session_id)

        h.send(type="gesture", gesture="PALM")
        self.assertEqual(h.m.state, IDLE)
        self.assertTrue(h.m.session_id.startswith("S"))

        for shot in (1, 2):
            h.send(type="gesture", gesture="V_SIGN") if shot == 1 else None
            if shot == 2:
                h.run(1.6)                       # pause between shots
            self.assertEqual(h.m.state, COUNTDOWN)
            self.assertEqual(h.m.shot, shot)
            h.run(3.1)                           # 3, 2, 1
            self.assertEqual(h.m.state, CAPTURE)
            capture = h.of("capture")[-1]
            self.assertEqual(capture["shot"], shot)
            h.run(0.6)                           # reach capture_at
            self.assertIn("SHUTTER", h.arduino())
            h.cameras_report()
            h.run(0.1)

        self.assertEqual(h.m.state, SELECT)
        h.send(type="gesture", gesture="FINGERS", value=2)
        h.send(type="gesture", gesture="THUMBS_UP")
        self.assertEqual(h.m.state, COMPOSE)
        self.assertEqual(h.of("compose")[-1]["frame_color"], "pink")

        h.send(type="compose_result", session_id=h.m.session_id, status="OK", url="http://x/1")
        self.assertEqual(h.m.state, DONE)
        self.assertEqual(h.of("state")[-1]["url"], "http://x/1")

        h.run(20.1)
        self.assertEqual(h.m.state, IDLE)
        self.assertIsNone(h.m.session_id)

        counts = [c for c in h.arduino() if c.startswith("COUNT:")]
        self.assertEqual(counts, ["COUNT:3", "COUNT:2", "COUNT:1"] * 2)
        self.assertEqual(h.arduino().count("SHUTTER"), 2)
        self.assertIn("DONE", h.arduino())

    def test_capture_waits_for_capture_at(self):
        h = Harness()
        h.send(type="button")                    # SLEEP -> IDLE + session
        h.send(type="button")                    # button replaces V sign
        h.run(3.05)
        self.assertEqual(h.m.state, CAPTURE)
        self.assertNotIn("SHUTTER", h.arduino())
        capture_at_s = h.of("capture")[-1]["capture_at"] / 1000
        self.assertAlmostEqual(capture_at_s - h.clock.now, 0.5, delta=0.06)


class ButtonOnly(unittest.TestCase):
    def test_button_replaces_every_gesture(self):
        h = Harness(shots=1, expected_cameras=("node2",))
        h.send(type="button")
        self.assertEqual((h.m.state, bool(h.m.session_id)), (IDLE, True))
        h.send(type="button")
        self.assertEqual(h.m.state, COUNTDOWN)
        h.run(3.7)
        h.cameras_report()
        h.run(0.1)
        self.assertEqual(h.m.state, SELECT)
        h.send(type="button")
        self.assertEqual(h.m.state, COMPOSE)
        self.assertEqual(h.of("compose")[-1]["frame_color"], "white")


class Gaze(unittest.TestCase):
    def _to_end_of_countdown(self, h):
        h.send(type="button")
        h.send(type="button")
        h.send(type="gaze", gaze_ok=False)
        h.run(3.1)

    def test_warn_then_ok(self):
        h = Harness()
        self._to_end_of_countdown(h)
        self.assertEqual(h.m.state, COUNTDOWN)
        self.assertEqual(h.arduino()[-1], "GAZE:WARN")
        h.run(1.0)
        self.assertEqual(h.m.state, COUNTDOWN)       # still waiting
        h.send(type="gaze", gaze_ok=True)
        h.run(0.1)
        self.assertEqual(h.m.state, CAPTURE)
        self.assertIn("GAZE:OK", h.arduino())

    def test_never_looks_shoots_after_timeout(self):
        h = Harness()
        self._to_end_of_countdown(h)
        h.run(4.6)                               # warned at ~3.0 s, waits up to 5 s
        self.assertEqual(h.m.state, COUNTDOWN)
        h.run(0.6)
        self.assertEqual(h.m.state, CAPTURE)

    def test_no_gaze_data_does_not_block(self):
        h = Harness()
        h.send(type="button")
        h.send(type="button")
        h.run(3.1)
        self.assertEqual(h.m.state, CAPTURE)
        self.assertNotIn("GAZE:WARN", h.arduino())


class Failures(unittest.TestCase):
    def test_missing_camera_is_an_error(self):
        h = Harness()
        h.send(type="button")
        h.send(type="button")
        h.run(3.7)
        h.send(type="capture_done", session_id=h.m.session_id, shot=1, node="node2")
        h.run(10.1)
        self.assertEqual(h.m.state, ERROR)
        self.assertEqual(h.of("state")[-1]["error"], "no photo node3")
        self.assertTrue(h.arduino()[-1].startswith("ERROR:"))
        self.assertLessEqual(len(h.arduino()[-1]) - len("ERROR:"), 16)
        h.run(5.1)
        self.assertEqual(h.m.state, IDLE)

    def test_stale_capture_done_is_ignored(self):
        h = Harness(expected_cameras=("node2",))
        h.send(type="button")
        h.send(type="button")
        h.run(3.7)
        h.send(type="capture_done", session_id="S-old", shot=1, node="node2")
        h.send(type="capture_done", session_id=h.m.session_id, shot=2, node="node2")
        h.run(0.1)
        self.assertEqual(h.m.state, CAPTURE)

    def test_compose_error_and_timeout(self):
        h = Harness(shots=1, expected_cameras=("node2",))
        for _ in range(2):
            h.send(type="button")
        h.run(3.7)
        h.cameras_report()
        h.run(0.1)
        h.send(type="button")
        h.send(type="compose_result", session_id=h.m.session_id, status="ERROR", error="disk full")
        self.assertEqual((h.m.state, h.m.error), (ERROR, "disk full"))

        h2 = Harness(shots=1, expected_cameras=("node2",))
        for _ in range(2):
            h2.send(type="button")
        h2.run(3.7)
        h2.cameras_report()
        h2.run(0.1)
        h2.send(type="button")
        h2.run(30.1)
        self.assertEqual((h2.m.state, h2.m.error), (ERROR, "compose timeout"))


class SleepAndWake(unittest.TestCase):
    def test_leave_sleeps_only_without_session(self):
        h = Harness()
        h.send(type="proximity", event="WAKE_UP")
        h.send(type="proximity", event="LEAVE")
        self.assertEqual(h.m.state, SLEEP)

        h.send(type="proximity", event="WAKE_UP")
        h.send(type="gesture", gesture="PALM")
        h.send(type="proximity", event="LEAVE")      # stepped back for the photo
        self.assertEqual(h.m.state, IDLE)
        self.assertIsNotNone(h.m.session_id)

    def test_idle_timeout(self):
        h = Harness()
        h.send(type="proximity", event="WAKE_UP")
        h.run(59.9)
        self.assertEqual(h.m.state, IDLE)
        h.run(0.2)
        self.assertEqual(h.m.state, SLEEP)
        self.assertEqual(h.arduino()[-1], "STATE:SLEEP")

    def test_wake_up_ignored_when_busy(self):
        h = Harness()
        h.send(type="button")
        h.send(type="button")
        h.send(type="proximity", event="WAKE_UP")
        self.assertEqual(h.m.state, COUNTDOWN)

    def test_palm_while_asleep_starts_session(self):
        h = Harness()
        h.send(type="gesture", gesture="PALM")
        self.assertEqual(h.states()[-2:], [IDLE, IDLE])
        self.assertIsNotNone(h.m.session_id)


class Layout(unittest.TestCase):
    def test_layout_follows_face_count(self):
        h = Harness()
        h.send(type="button")
        h.send(type="faces", face_count=2)
        self.assertEqual(h.of("state")[-1]["layout"], "vertical")
        h.send(type="faces", face_count=3)
        self.assertEqual(h.of("state")[-1]["layout"], "wide")
        h.send(type="faces", face_count=0)              # nobody detected: keep last
        self.assertEqual(h.m.layout, "wide")

    def test_frame_choice_updates_state(self):
        h = Harness(shots=1, expected_cameras=("node2",))
        h.send(type="button")
        h.send(type="button")
        h.run(3.7)
        h.cameras_report()
        h.run(0.1)
        h.send(type="gesture", gesture="FINGERS", value=3)
        self.assertEqual(h.of("state")[-1]["frame_color"], "blue")
        self.assertEqual(h.arduino()[-1], "LCD:Frame: blue|Thumbs up = OK")
        h.run(20.1)                                     # no thumbs-up: auto confirm
        self.assertEqual(h.of("compose")[-1]["frame_color"], "blue")


class ArduinoText(unittest.TestCase):
    def test_lcd_lines_fit_16_chars(self):
        h = Harness(shots=2, expected_cameras=("node2",))
        h.send(type="button")
        h.send(type="button")
        for _ in range(2):
            h.run(3.7)
            h.cameras_report()
            h.run(1.7)
        for value in (1, 2, 3, 4):
            h.send(type="gesture", gesture="FINGERS", value=value)
        for cmd in h.arduino():
            if cmd.startswith("LCD:"):
                for line in cmd[4:].split("|"):
                    self.assertLessEqual(len(line), 16, cmd)
                    self.assertTrue(line.isascii(), cmd)


if __name__ == "__main__":
    unittest.main()
