"""발표용 샘플 사진. 실기 카메라가 없을 때 합성 파이프라인을 보여 주기 위한 그림이다."""

from __future__ import annotations

from PIL import Image, ImageDraw

from node4.composer import ShotInput


CURTAINS = [
    ((244, 164, 176), (255, 236, 228)),
    ((126, 160, 196), (228, 238, 246)),
    ((132, 196, 170), (232, 246, 238)),
    ((188, 170, 224), (244, 238, 255)),
]
HAIR = [(58, 42, 36), (92, 62, 48), (36, 36, 40), (120, 72, 48)]
SHIRTS = [(232, 96, 110), (48, 86, 168), (236, 176, 64), (64, 150, 132)]
PANTS = [(54, 58, 72), (42, 48, 64), (72, 64, 58), (48, 62, 70)]
SKIN = [(255, 214, 190), (240, 196, 168), (214, 170, 140), (255, 206, 186)]


def make_session(
    session_id: str,
    people: int,
    scores: list[float],
    face_count: int | None = None,
) -> list[ShotInput]:
    """정면·보조 × 2회, 총 4장. scores 순서는 촬영 순서다."""
    order = ((1, "front"), (1, "side"), (2, "front"), (2, "side"))
    shots = []
    count = people if face_count is None else face_count
    for index, ((shot, angle), score) in enumerate(zip(order, scores)):
        curtain, wall = CURTAINS[index]
        image, faces = render_photo(
            people=people,
            smile=score,
            facing="front" if angle == "front" else "left",
            curtain=curtain,
            wall=wall,
            variant=index,
        )
        shots.append(ShotInput(
            image=image,
            shot=shot,
            angle=angle,
            filename=f"{session_id}_{shot}_{angle}.jpg",
            smile_score=score,
            face_count=count if angle == "front" else None,
            faces=faces,
        ))
    return shots


def render_photo(
    *,
    people: int,
    smile: float,
    facing: str,
    curtain: tuple[int, int, int],
    wall: tuple[int, int, int],
    variant: int,
    size: tuple[int, int] = (1200, 800),
) -> tuple[Image.Image, list[dict]]:
    width, height = size
    image = _gradient(size, wall, _mix(wall, curtain, 0.35))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, int(height * 0.16)], fill=curtain)
    draw.rectangle([0, int(height * 0.16), width, int(height * 0.20)], fill=_mix(curtain, (255, 255, 255), 0.45))
    floor = _shade(wall, 0.86)
    draw.rectangle([0, int(height * 0.80), width, height], fill=floor)

    if facing == "front":
        origin, span = 0.50, 0.46 if people >= 3 else 0.34
    else:
        origin, span = 0.36, 0.40 if people >= 3 else 0.30
    if people == 1:
        centers = [origin]
    else:
        step = span / (people - 1)
        start = origin - span / 2
        centers = [start + step * i for i in range(people)]

    scale = 0.78 if people >= 3 else 0.92
    faces = []
    ground_y = height - 28
    for index, center in enumerate(centers):
        box = _draw_person(
            draw,
            cx=int(width * center),
            ground_y=ground_y,
            scale=scale,
            smile=smile - index * 6,
            hair=HAIR[(variant + index) % len(HAIR)],
            shirt=SHIRTS[(variant + index) % len(SHIRTS)],
            pants=PANTS[(variant + index) % len(PANTS)],
            skin=SKIN[(variant + index) % len(SKIN)],
            facing=facing,
        )
        faces.append(box)
    return image, faces


def _draw_person(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    ground_y: int,
    scale: float,
    smile: float,
    hair: tuple[int, int, int],
    shirt: tuple[int, int, int],
    pants: tuple[int, int, int],
    skin: tuple[int, int, int],
    facing: str,
) -> dict:
    def s(value: float) -> int:
        return int(round(value * scale))

    smile = max(0, min(100, smile))
    head_r = s(64)
    head_cy = ground_y - s(392)
    shift = s(-18) if facing == "left" else 0
    shadow = (210, 196, 184)
    draw.ellipse([cx - s(78), ground_y - s(16), cx + s(78), ground_y + s(12)], fill=shadow)

    leg_top = ground_y - s(168)
    draw.rounded_rectangle([cx - s(46), leg_top, cx - s(8), ground_y], radius=s(10), fill=pants)
    draw.rounded_rectangle([cx + s(8), leg_top, cx + s(46), ground_y], radius=s(10), fill=pants)
    draw.rounded_rectangle([cx - s(18), ground_y - s(14), cx - s(2), ground_y + s(4)], radius=4, fill=(70, 70, 74))
    draw.rounded_rectangle([cx + s(2), ground_y - s(14), cx + s(18), ground_y + s(4)], radius=4, fill=(70, 70, 74))

    body_top = head_cy + s(48)
    body_bottom = leg_top + s(36)
    draw.rounded_rectangle(
        [cx - s(78), body_top, cx + s(78), body_bottom],
        radius=s(28),
        fill=shirt,
    )
    # 팔
    draw.rounded_rectangle([cx - s(108), body_top + s(16), cx - s(70), body_bottom - s(10)], radius=s(16), fill=shirt)
    draw.rounded_rectangle([cx + s(70), body_top + s(16), cx + s(108), body_bottom - s(10)], radius=s(16), fill=shirt)
    draw.ellipse([cx - s(118), body_top + s(78), cx - s(86), body_top + s(112)], fill=skin)
    draw.ellipse([cx + s(86), body_top + s(78), cx + s(118), body_top + s(112)], fill=skin)

    draw.rounded_rectangle([cx - s(16), head_cy + head_r - s(8), cx + s(16), body_top + s(16)], radius=8, fill=skin)

    hx = cx + shift
    draw.ellipse([hx - head_r, head_cy - head_r, hx + head_r, head_cy + head_r], fill=skin)
    # 머리카락
    draw.pieslice(
        [hx - head_r - s(2), head_cy - head_r - s(6), hx + head_r + s(2), head_cy + s(8)],
        start=180,
        end=360,
        fill=hair,
    )
    draw.ellipse([hx - head_r - s(4), head_cy - s(10), hx - head_r + s(18), head_cy + s(28)], fill=skin)
    draw.ellipse([hx + head_r - s(18), head_cy - s(10), hx + head_r + s(4), head_cy + s(28)], fill=skin)

    eye_y = head_cy - s(6)
    eye_dx = s(24)
    eye_w = s(12)
    eye_h = s(14) if smile < 70 else s(8)
    for sign in (-1, 1):
        ex = hx + shift // 3 + sign * eye_dx
        draw.ellipse([ex - eye_w, eye_y - eye_h, ex + eye_w, eye_y + eye_h], fill=(45, 42, 48))
        draw.ellipse([ex - s(3), eye_y - eye_h + s(2), ex + s(2), eye_y - eye_h + s(8)], fill=(255, 255, 255))

    if smile > 55:
        blush = (255, 170, 170)
        draw.ellipse([hx - s(48), head_cy + s(10), hx - s(28), head_cy + s(24)], fill=blush)
        draw.ellipse([hx + s(28), head_cy + s(10), hx + s(48), head_cy + s(24)], fill=blush)

    mouth_w = s(18 + smile * 0.28)
    mouth_h = s(8 + smile * 0.16)
    mouth_y = head_cy + s(24)
    mx = hx + shift // 4
    draw.arc(
        [mx - mouth_w, mouth_y - mouth_h // 2, mx + mouth_w, mouth_y + mouth_h],
        start=15,
        end=165,
        fill=(168, 72, 86),
        width=max(2, s(4)),
    )

    box_w = head_r * 2
    box_h = head_r * 2
    return {
        "x": int(hx - head_r),
        "y": int(head_cy - head_r),
        "w": int(box_w),
        "h": int(box_h),
    }


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    height = size[1]
    column = Image.new("RGB", (1, height))
    pixels = column.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        pixels[0, y] = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
    return column.resize(size, Image.Resampling.BILINEAR)


def _mix(a, b, t: float):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _shade(color, factor: float):
    return tuple(max(0, min(255, int(channel * factor))) for channel in color)
