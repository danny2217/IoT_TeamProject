"""Node 4 합성 규칙과 HTTP 입구 테스트."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from node4.composer import (  # noqa: E402
    ComposeError,
    ShotInput,
    compose_shots,
    face_center_crop,
    layout_for_face_count,
    layout_spec,
)
from node4.mqtt_node import CAPTURE_DONE, COMPOSE_REQUEST, handle_message  # noqa: E402
from node4.server import create_app  # noqa: E402
from node4.service import Node4Service, image_bytes  # noqa: E402
from node4.storage import Store  # noqa: E402


def solid(color, size=(240, 160)):
    return Image.new("RGB", size, color)


def four_shots(scores=(10, 20, 99, 30), face_count=2):
    colors = [(200, 30, 30), (30, 180, 30), (30, 30, 210), (220, 200, 20)]
    order = [(1, "front"), (1, "side"), (2, "front"), (2, "side")]
    shots = []
    for (shot, angle), color, score in zip(order, colors, scores):
        shots.append(ShotInput(
            image=solid(color),
            shot=shot,
            angle=angle,
            filename=f"S_{shot}_{angle}.jpg",
            smile_score=score,
            face_count=face_count if angle == "front" else None,
        ))
    return shots


class ComposerTests(unittest.TestCase):
    def test_layout_follows_face_count(self):
        self.assertEqual(layout_for_face_count(0), "vertical")
        self.assertEqual(layout_for_face_count(2), "vertical")
        self.assertEqual(layout_for_face_count(3), "wide")
        self.assertEqual(layout_for_face_count(6), "wide")

    def test_missing_shot_names_the_gap(self):
        with self.assertRaises(ComposeError) as caught:
            compose_shots(four_shots()[:3], frame_color="white")
        self.assertEqual(str(caught.exception), "missing shot 2 side")

    def test_best_smile_is_first_slot_and_layout_changes(self):
        vertical = compose_shots(four_shots(face_count=2), frame_color="pink")
        self.assertEqual(vertical.layout, "vertical")
        self.assertEqual(vertical.best_shot, "S_2_front.jpg")
        self.assertGreater(vertical.image.height, vertical.image.width)
        slot = layout_spec("vertical")["slots"][0]
        pixel = vertical.image.getpixel((slot[0] + slot[2] // 2, slot[1] + slot[3] // 2))
        self.assertGreater(pixel[2], 180)
        self.assertLess(pixel[0], 80)

        wide = compose_shots(four_shots(face_count=3), frame_color="blue")
        self.assertEqual(wide.layout, "wide")
        self.assertGreater(wide.image.width, wide.image.height)

    def test_frame_color_paints_the_border(self):
        black = compose_shots(four_shots(), frame_color="black").image
        pink = compose_shots(four_shots(), frame_color="pink").image
        self.assertEqual(black.getpixel((0, 0)), (20, 20, 22))
        self.assertEqual(pink.getpixel((0, 0)), (255, 226, 234))
        self.assertNotEqual(black.getpixel((0, 0)), pink.getpixel((0, 0)))

    def test_offcenter_face_moves_to_the_middle(self):
        image = Image.new("RGB", (300, 200), (0, 0, 255))
        ImageDraw.Draw(image).rectangle([240, 70, 279, 109], fill=(255, 0, 0))
        cropped = face_center_crop(image, [{"x": 240, "y": 70, "w": 40, "h": 40}], 100, 100)
        red, _green, blue = cropped.getpixel((50, 50))
        self.assertGreater(red, 200)
        self.assertLess(blue, 80)


class PipelineTests(unittest.TestCase):
    def test_http_compose_download_and_missing_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Node4Service(Store(tmp), public_base="http://127.0.0.1:8080")
            client = create_app(service).test_client()
            session = "S20261111-150000"
            order = [(1, "front", 40), (1, "side", 50), (2, "front", 96), (2, "side", 20)]
            for shot, angle, score in order:
                response = client.post("/api/photos", data={
                    "session_id": session,
                    "shot": str(shot),
                    "angle": angle,
                    "node": "node2" if angle == "front" else "node3",
                    "smile_score": str(score),
                    "face_count": "2" if angle == "front" else "",
                    "file": (io.BytesIO(image_bytes(solid((10, 20, score)))), "a.jpg"),
                }, content_type="multipart/form-data")
                self.assertEqual(response.status_code, 200, response.data)

            composed = client.post("/api/compose", json={"session_id": session, "frame_color": "white"})
            body = composed.get_json()
            self.assertEqual(body["status"], "OK")
            self.assertEqual(body["layout"], "vertical")
            self.assertEqual(body["best_shot"], f"{session}_2_front.jpg")
            self.assertTrue(body["url"].endswith(f"/download/{session}"))

            page = client.get(f"/download/{session}")
            self.assertEqual(page.status_code, 200)
            self.assertIn("촬영 결과".encode(), page.data)
            self.assertEqual(client.get(f"/results/{session}.jpg").status_code, 200)
            self.assertEqual(client.get(f"/qr/{session}.png").status_code, 200)

            stored = service.store.get_session(session)
            self.assertEqual(stored["storage"], "local")
            self.assertEqual(stored["frame_color"], "white")
            self.assertEqual(len(stored["shot_scores"]), 4)

            short = "S20261111-150001"
            client.post("/api/photos", data={
                "session_id": short,
                "shot": "1",
                "angle": "front",
                "smile_score": "10",
                "face_count": "3",
                "file": (io.BytesIO(image_bytes(solid((1, 2, 3)))), "a.jpg"),
            }, content_type="multipart/form-data")
            failed = client.post("/api/compose", json={"session_id": short, "frame_color": "black"})
            self.assertEqual(failed.get_json()["status"], "ERROR")
            self.assertIn("missing shot 1 side", failed.get_json()["error"])

            bad = client.post("/api/compose", json={"session_id": session, "frame_color": "gold"})
            self.assertEqual(bad.status_code, 400)

    def test_capture_done_metadata_reaches_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Node4Service(Store(tmp))
            session = "S20261111-160000"
            order = [(1, "front"), (1, "side"), (2, "front"), (2, "side")]
            for shot, angle in order:
                service.add_photo(
                    session_id=session,
                    shot=shot,
                    angle=angle,
                    data=image_bytes(solid((80, 80, 80))),
                    face_count=3 if angle == "front" else None,
                )
            handle_message(service, CAPTURE_DONE, {
                "session_id": session,
                "shot": 2,
                "angle": "front",
                "node": "node2",
                "file": f"{session}_2_front.jpg",
                "face_count": 3,
                "smile_score": 95,
                "gaze_ok": True,
                "faces": [],
            })
            result = handle_message(service, COMPOSE_REQUEST, {
                "session_id": session,
                "frame_color": "blue",
            })
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["layout"], "wide")
            self.assertEqual(result["best_shot"], f"{session}_2_front.jpg")
            self.assertIsNone(result["error"])
            raw = json.dumps(result)
            self.assertNotIn("__", raw)


if __name__ == "__main__":
    unittest.main()
