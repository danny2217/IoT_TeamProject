"""네컷 합성.

제안서 6.5와 MQTT 규약 v1:
- face_count <= 2 이면 세로 4컷, 3 이상이면 2x2 와이드
- frame_color: white(1) pink(2) blue(3) black(4)
- smile_score가 가장 높은 컷을 첫 칸(세로=맨 위, 와이드=왼쪽 위)에 두고 Best Smile 뱃지
- 얼굴 영역이 칸 중앙에 오도록 크롭. 얼굴이 없으면 중앙 크롭
- 날짜와 팀 표시를 프레임 아래에 넣는다
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date

from PIL import Image, ImageDraw, ImageFont

FRAME_COLORS = {
    "white": {
        "bg": (255, 255, 255),
        "ink": (42, 42, 46),
        "muted": (110, 110, 116),
        "accent": (214, 58, 102),
        "badge_ink": (255, 255, 255),
        "line": (210, 210, 214),
    },
    "pink": {
        "bg": (255, 226, 234),
        "ink": (112, 36, 62),
        "muted": (168, 86, 112),
        "accent": (214, 52, 104),
        "badge_ink": (255, 255, 255),
        "line": (255, 255, 255),
    },
    "blue": {
        "bg": (222, 236, 252),
        "ink": (22, 54, 108),
        "muted": (70, 108, 158),
        "accent": (28, 104, 198),
        "badge_ink": (255, 255, 255),
        "line": (255, 255, 255),
    },
    "black": {
        "bg": (20, 20, 22),
        "ink": (245, 245, 245),
        "muted": (176, 176, 182),
        "accent": (255, 196, 72),
        "badge_ink": (24, 24, 26),
        "line": (58, 58, 62),
    },
}

# 칸 높이 대비 얼굴(또는 얼굴 묶음) 높이. 작을수록 더 확대된다.
FACE_HEIGHT_RATIO = 0.34
REQUIRED_SHOTS = ((1, "front"), (1, "side"), (2, "front"), (2, "side"))


class ComposeError(Exception):
    """합성할 수 없을 때. message는 MQTT error 필드에 그대로 넣는다."""


@dataclass
class ShotInput:
    image: Image.Image
    shot: int
    angle: str
    filename: str
    smile_score: float | None = None
    face_count: int | None = None
    faces: list[dict] | None = None


@dataclass
class ComposeResult:
    image: Image.Image
    layout: str
    face_count: int
    frame_color: str
    best_shot: str
    ordered_files: list[str] = field(default_factory=list)
    shot_scores: list[dict] = field(default_factory=list)


def layout_for_face_count(face_count: int) -> str:
    """2명 이하면 세로 4컷, 3명 이상이면 2x2."""
    return "vertical" if int(face_count) <= 2 else "wide"


def layout_spec(layout: str) -> dict:
    """캔버스 크기와 네 칸의 (x, y, w, h)."""
    if layout == "vertical":
        size = (900, 2400)
        margin = (36, 32, 36, 24)
        footer_h = 132
        gap = 14
        cols, rows = 1, 4
    elif layout == "wide":
        size = (1800, 1360)
        margin = (40, 36, 40, 24)
        footer_h = 120
        gap = 16
        cols, rows = 2, 2
    else:
        raise ValueError(f"unknown layout: {layout}")

    width, height = size
    left, top, right, bottom = margin
    avail_w = width - left - right - gap * (cols - 1)
    avail_h = height - top - bottom - footer_h - gap * (rows - 1)
    slot_w = avail_w // cols
    slot_h = avail_h // rows
    slots = []
    for row in range(rows):
        for col in range(cols):
            x = left + col * (slot_w + gap)
            y = top + row * (slot_h + gap)
            slots.append((x, y, slot_w, slot_h))
    footer_top = top + rows * slot_h + (rows - 1) * gap + 16
    return {
        "size": size,
        "slots": slots,
        "footer_top": footer_top,
        "footer_h": footer_h,
    }


def representative_face_count(shots: list[ShotInput]) -> int:
    """정면 컷 face_count의 최댓값. 정면에 없으면 전체에서 고른다."""
    front = [s.face_count for s in shots if s.angle == "front" and s.face_count is not None]
    pool = front or [s.face_count for s in shots if s.face_count is not None]
    return int(max(pool)) if pool else 1


def _shot_key(shot: int, angle: str) -> tuple[int, str]:
    return int(shot), angle


def missing_shot_names(shots: list[ShotInput]) -> list[str]:
    have = {_shot_key(s.shot, s.angle) for s in shots}
    missing = []
    for shot, angle in REQUIRED_SHOTS:
        if (shot, angle) not in have:
            missing.append(f"shot {shot} {angle}")
    return missing


def _score(shot: ShotInput) -> float:
    return float(shot.smile_score) if shot.smile_score is not None else -1.0


def order_shots(shots: list[ShotInput]) -> list[ShotInput]:
    """베스트 컷을 첫 칸에 두고, 나머지는 촬영 순서를 유지한다."""
    missing = missing_shot_names(shots)
    if missing:
        raise ComposeError("missing " + ", ".join(missing))
    by_key = {_shot_key(s.shot, s.angle): s for s in shots}
    ordered = [by_key[key] for key in REQUIRED_SHOTS]
    best = max(ordered, key=lambda s: (_score(s), 1 if s.angle == "front" else 0, s.shot))
    rest = [s for s in ordered if s is not best]
    return [best, *rest]


def focus_box(faces: list[dict] | None) -> dict | None:
    """여러 얼굴이면 모두를 포함하는 사각형. 한 명도 잘리지 않게 맞춘다."""
    if not faces:
        return None
    x0 = min(int(face["x"]) for face in faces)
    y0 = min(int(face["y"]) for face in faces)
    x1 = max(int(face["x"]) + int(face["w"]) for face in faces)
    y1 = max(int(face["y"]) + int(face["h"]) for face in faces)
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def face_center_crop(image: Image.Image, faces: list[dict] | None, slot_w: int, slot_h: int) -> Image.Image:
    """슬롯 비율에 맞추고, 얼굴 묶음의 중심이 칸 가운데에 오도록 자른다."""
    img = image.convert("RGB")
    src_w, src_h = img.size
    aspect = slot_w / slot_h
    box = focus_box(faces)

    if box is None:
        crop_w, crop_h = _largest_crop(src_w, src_h, aspect)
        left = (src_w - crop_w) // 2
        top = (src_h - crop_h) // 2
    else:
        face_w = max(1, box["w"])
        face_h = max(1, box["h"])
        cover_w = face_w * 1.45
        cover_h = face_h * 1.45
        pref_h = face_h / FACE_HEIGHT_RATIO
        pref_w = pref_h * aspect
        crop_w = max(pref_w, cover_w, cover_h * aspect)
        crop_h = crop_w / aspect
        if crop_h < cover_h:
            crop_h = cover_h
            crop_w = crop_h * aspect
        crop_w, crop_h = _clamp_crop(crop_w, crop_h, src_w, src_h, aspect)
        cx = box["x"] + box["w"] / 2
        cy = box["y"] + box["h"] / 2
        left = int(round(cx - crop_w / 2))
        top = int(round(cy - crop_h / 2))
        left = max(0, min(left, src_w - crop_w))
        top = max(0, min(top, src_h - crop_h))

    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((slot_w, slot_h), Image.Resampling.LANCZOS)


def _largest_crop(src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    if src_w / src_h > aspect:
        crop_h = src_h
        crop_w = max(1, int(round(src_h * aspect)))
    else:
        crop_w = src_w
        crop_h = max(1, int(round(src_w / aspect)))
    return min(crop_w, src_w), min(crop_h, src_h)


def _clamp_crop(crop_w: float, crop_h: float, src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    if crop_w > src_w or crop_h > src_h:
        if src_w / src_h > aspect:
            crop_h = float(src_h)
            crop_w = crop_h * aspect
        else:
            crop_w = float(src_w)
            crop_h = crop_w / aspect
    crop_w = max(1, min(int(round(crop_w)), src_w))
    crop_h = max(1, min(int(round(crop_h)), src_h))
    if crop_w / crop_h > aspect:
        crop_w = max(1, min(src_w, int(round(crop_h * aspect))))
    else:
        crop_h = max(1, min(src_h, int(round(crop_w / aspect))))
    return crop_w, crop_h


def compose_shots(
    shots: list[ShotInput],
    *,
    frame_color: str = "white",
    when: date | None = None,
    sample_note: bool = False,
    face_count: int | None = None,
) -> ComposeResult:
    color_name = (frame_color or "white").lower()
    if color_name not in FRAME_COLORS:
        raise ValueError(f"unknown frame_color: {frame_color}")

    ordered = order_shots(shots)
    count = representative_face_count(shots) if face_count is None else int(face_count)
    layout = layout_for_face_count(count)
    spec = layout_spec(layout)
    palette = FRAME_COLORS[color_name]
    canvas = Image.new("RGB", spec["size"], palette["bg"])
    draw = ImageDraw.Draw(canvas)

    for shot, (x, y, slot_w, slot_h) in zip(ordered, spec["slots"]):
        photo = face_center_crop(shot.image, shot.faces, slot_w, slot_h)
        canvas.paste(photo, (x, y))
        draw.rectangle([x, y, x + slot_w - 1, y + slot_h - 1], outline=palette["line"], width=3)

    _draw_badge(draw, spec["slots"][0], palette)
    _draw_footer(draw, spec, palette, when or date.today(), sample_note)

    scores = [
        {
            "file": shot.filename,
            "shot": shot.shot,
            "angle": shot.angle,
            "smile_score": shot.smile_score,
        }
        for shot in ordered
    ]
    return ComposeResult(
        image=canvas,
        layout=layout,
        face_count=count,
        frame_color=color_name,
        best_shot=ordered[0].filename,
        ordered_files=[shot.filename for shot in ordered],
        shot_scores=scores,
    )


def _draw_badge(draw: ImageDraw.ImageDraw, slot: tuple[int, int, int, int], palette: dict) -> None:
    x, y, _w, _h = slot
    font = _font(28, bold=True)
    text = "Best Smile"
    tw, th = _text_size(draw, text, font)
    pad_x, pad_y = 16, 8
    left = x + 18
    top = y + 18
    box = [left, top, left + tw + pad_x * 2, top + th + pad_y * 2]
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=16, fill=palette["accent"])
    else:
        draw.rectangle(box, fill=palette["accent"])
    draw.text((left + pad_x, top + pad_y - 2), text, font=font, fill=palette["badge_ink"])


def _draw_footer(draw, spec, palette, when: date, sample_note: bool) -> None:
    top = spec["footer_top"]
    title_font = _font(32, bold=True)
    sub_font = _font(22, bold=False)
    title = "다각도 스마트 포토부스"
    sub = when.strftime("%Y.%m.%d") + "  ·  2조"
    if sample_note:
        sub += "  ·  PC 샘플 검증"
    logo = 54
    lx = 48
    ly = top + 8
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle([lx, ly, lx + logo, ly + logo], radius=12, fill=palette["accent"])
    else:
        draw.rectangle([lx, ly, lx + logo, ly + logo], fill=palette["accent"])
    mark_font = _font(22, bold=True)
    mw, mh = _text_size(draw, "PB", mark_font)
    draw.text((lx + (logo - mw) / 2, ly + (logo - mh) / 2 - 2), "PB", font=mark_font, fill=palette["badge_ink"])
    draw.text((lx + logo + 16, ly - 2), title, font=title_font, fill=palette["ink"])
    draw.text((lx + logo + 16, ly + 36), sub, font=sub_font, fill=palette["muted"])


def _font(size: int, bold: bool) -> ImageFont.ImageFont:
    names = ["malgunbd.ttf", "malgun.ttf"] if bold else ["malgun.ttf", "malgunbd.ttf"]
    windows = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [os.path.join(windows, "Fonts", name) for name in names]
    candidates += [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    if hasattr(draw, "textbbox"):
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    return font.getsize(text)
