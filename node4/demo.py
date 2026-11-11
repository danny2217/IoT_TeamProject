"""PC에서 샘플 4장으로 세로·와이드 합성을 만들고 발표용 이미지를 저장한다."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from node4.composer import _font
from node4.samples import make_session
from node4.service import Node4Service, image_bytes
from node4.storage import Store


SESSIONS = (
    {
        "session_id": "S20261111-140201",
        "people": 2,
        "scores": [91, 64, 73, 55],
        "frame_color": "pink",
        "export": "vertical_face2_pink.jpg",
    },
    {
        "session_id": "S20261111-140202",
        "people": 3,
        "scores": [70, 48, 86, 60],
        "frame_color": "blue",
        "export": "wide_face3_blue.jpg",
    },
)


def run_demo(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    service = Node4Service(Store(out_dir / "booth_data"), public_base="http://127.0.0.1:8080", sample_note=True)
    summary = []
    images = []
    for spec in SESSIONS:
        shots = make_session(spec["session_id"], spec["people"], spec["scores"])
        for shot in shots:
            service.add_photo(
                session_id=spec["session_id"],
                shot=shot.shot,
                angle=shot.angle,
                data=image_bytes(shot.image),
                node="node2" if shot.angle == "front" else "node3",
                filename=shot.filename,
                face_count=shot.face_count,
                smile_score=shot.smile_score,
                gaze_ok=True,
                faces=shot.faces,
            )
        payload = service.compose(spec["session_id"], spec["frame_color"])
        src = Path(service.store.get_session(spec["session_id"])["file_path"])
        dest = out_dir / spec["export"]
        dest.write_bytes(src.read_bytes())
        images.append(dest)
        summary.append({
            "file": spec["export"],
            "session_id": spec["session_id"],
            "face_count": spec["people"],
            "frame_color": spec["frame_color"],
            "layout": payload["layout"],
            "best_shot": payload["best_shot"],
            "url": payload["url"],
            "status": payload["status"],
        })

    panel = out_dir / "slide12_samples.png"
    _write_panel(panel, images, summary)
    manifest = {
        "verified_on": "PC",
        "hardware": "라즈베리파이·카메라는 사용하지 않음",
        "aws": "버킷 환경변수가 없어 로컬 SQLite와 파일 저장으로 검증",
        "results": summary,
        "slide_panel": panel.name,
    }
    (out_dir / "result.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_panel(path: Path, images: list[Path], summary: list[dict]) -> None:
    canvas = Image.new("RGB", (1920, 1080), (248, 246, 243))
    draw = ImageDraw.Draw(canvas)
    title_font = _font(42, bold=True)
    sub_font = _font(24, bold=False)
    cap_font = _font(26, bold=True)
    draw.text((72, 48), "Node 4  샘플 합성 검증", font=title_font, fill=(28, 28, 32))
    draw.text(
        (72, 108),
        "PC에서 샘플 사진 4장 · face_count로 레이아웃 자동 선택 · Best Smile은 첫 칸",
        font=sub_font,
        fill=(90, 90, 96),
    )
    captions = [
        "face_count = 2  →  세로 4컷  ·  pink",
        "face_count = 3  →  2×2 와이드  ·  blue",
    ]
    boxes = [(80, 190, 760, 980), (860, 190, 1840, 980)]
    for dest, caption, (x0, y0, x1, y1) in zip(images, captions, boxes):
        photo = Image.open(dest).convert("RGB")
        fitted = _fit(photo, x1 - x0, y1 - y0 - 56)
        px = x0 + ((x1 - x0) - fitted.width) // 2
        py = y0
        canvas.paste(fitted, (px, py))
        draw.text((x0, y1 - 44), caption, font=cap_font, fill=(32, 32, 36))
    canvas.save(path, "PNG")


def _fit(image: Image.Image, max_w: int, max_h: int) -> Image.Image:
    scale = min(max_w / image.width, max_h / image.height)
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)
