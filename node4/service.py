"""사진 수집과 합성을 HTTP·MQTT가 같이 쓰는 서비스."""

from __future__ import annotations

import json
import os
import re
import time
from io import BytesIO
from pathlib import Path

import qrcode
from PIL import Image, UnidentifiedImageError

from node4.composer import FRAME_COLORS, REQUIRED_SHOTS, ComposeError, ShotInput, compose_shots
from node4.faces import detect_faces, estimate_smile_score
from node4.storage import Store, upload_result_to_s3

SESSION_RE = re.compile(r"^S[A-Za-z0-9-]{1,48}$")
ANGLES = {"front", "side"}
SHOTS = {1, 2}


def now_ms() -> int:
    return int(time.time() * 1000)


class Node4Service:
    def __init__(self, store: Store, public_base: str | None = None, sample_note: bool = False):
        self.store = store
        base = public_base or os.environ.get("BOOTH_PUBLIC_BASE", "http://127.0.0.1:8080")
        self.public_base = base.rstrip("/")
        self.sample_note = sample_note

    def add_photo(
        self,
        *,
        session_id: str,
        shot: int,
        angle: str,
        data: bytes,
        node: str | None = None,
        filename: str | None = None,
        face_count: int | None = None,
        smile_score: float | None = None,
        gaze_ok: bool | None = None,
        faces: list | None = None,
    ) -> dict:
        self._check_identity(session_id, shot, angle)
        name = filename or f"{session_id}_{shot}_{angle}.jpg"
        path = self.store.upload_dir / f"{session_id}_{shot}_{angle}.jpg"
        path.write_bytes(data)
        return self.store.upsert_shot(
            session_id=session_id,
            shot=shot,
            angle=angle,
            node=node,
            filename=name,
            file_path=str(path),
            face_count=face_count,
            smile_score=smile_score,
            gaze_ok=gaze_ok,
            faces=faces,
        )

    def note_capture(self, payload: dict) -> dict | None:
        """booth/capture/done. 파일 바이트는 HTTP로 이미 왔거나 나중에 온다."""
        session_id = payload.get("session_id")
        shot = payload.get("shot")
        angle = payload.get("angle")
        if session_id is None or shot is None or angle is None:
            return None
        self._check_identity(str(session_id), int(shot), str(angle))
        faces = payload.get("faces")
        if isinstance(faces, str):
            faces = json.loads(faces)
        return self.store.upsert_shot(
            session_id=str(session_id),
            shot=int(shot),
            angle=str(angle),
            node=payload.get("node"),
            filename=payload.get("file"),
            face_count=_optional_int(payload.get("face_count")),
            smile_score=_optional_float(payload.get("smile_score")),
            gaze_ok=_optional_bool(payload.get("gaze_ok")),
            faces=faces if isinstance(faces, list) else None,
        )

    def compose(self, session_id: str, frame_color: str = "white") -> dict:
        if not SESSION_RE.match(session_id or ""):
            raise ValueError("session_id 형식이 올바르지 않습니다.")
        color = (frame_color or "white").lower()
        if color not in FRAME_COLORS:
            raise ValueError("frame_color는 white, pink, blue, black 중 하나여야 합니다.")

        try:
            shots = self._load_shots(session_id)
            result = compose_shots(shots, frame_color=color, sample_note=self.sample_note)
        except ComposeError as exc:
            payload = {
                "session_id": session_id,
                "status": "ERROR",
                "layout": None,
                "best_shot": None,
                "url": None,
                "error": str(exc),
                "ts": now_ms(),
            }
            self.store.save_session({
                "session_id": session_id,
                "frame_color": color,
                "status": "ERROR",
                "error": str(exc),
                "storage": "local",
            })
            return payload

        out_path = self.store.result_dir / f"{session_id}.jpg"
        result.image.save(out_path, "JPEG", quality=95, subsampling=0)
        s3_url = upload_result_to_s3(out_path, f"results/{session_id}.jpg")
        download_url = f"{self.public_base}/download/{session_id}"
        self._write_qr(session_id, download_url)
        self.store.save_session({
            "session_id": session_id,
            "layout": result.layout,
            "frame_color": color,
            "face_count": result.face_count,
            "best_shot": result.best_shot,
            "shot_scores": json.dumps(result.shot_scores, ensure_ascii=False),
            "file_path": str(out_path),
            "download_url": download_url,
            "s3_url": s3_url,
            "storage": "s3" if s3_url else "local",
            "status": "OK",
            "error": None,
        })
        return {
            "session_id": session_id,
            "status": "OK",
            "layout": result.layout,
            "best_shot": result.best_shot,
            "url": download_url,
            "error": None,
            "ts": now_ms(),
        }

    def _load_shots(self, session_id: str) -> list[ShotInput]:
        rows = { (row["shot"], row["angle"]): row for row in self.store.list_shots(session_id) }
        loaded: list[ShotInput] = []
        missing = []
        for shot, angle in REQUIRED_SHOTS:
            row = rows.get((shot, angle))
            path = row.get("file_path") if row else None
            if not path or not Path(path).is_file():
                missing.append(f"shot {shot} {angle}")
                continue
            try:
                with Image.open(path) as img:
                    image = img.convert("RGB")
            except (UnidentifiedImageError, OSError) as exc:
                raise ComposeError(f"unreadable shot {shot} {angle}") from exc
            faces = list(row.get("faces") or [])
            if not faces:
                faces = detect_faces(image)
            smile = row.get("smile_score")
            if smile is None:
                smile = estimate_smile_score(image, faces)
            loaded.append(ShotInput(
                image=image,
                shot=shot,
                angle=angle,
                filename=row.get("filename") or f"{session_id}_{shot}_{angle}.jpg",
                smile_score=smile,
                face_count=row.get("face_count"),
                faces=faces,
            ))
        if missing:
            raise ComposeError("missing " + ", ".join(missing))
        return loaded

    def _write_qr(self, session_id: str, url: str) -> Path:
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        path = self.store.qr_dir / f"{session_id}.png"
        image.save(path)
        return path

    def result_path(self, session_id: str) -> Path | None:
        session = self.store.get_session(session_id)
        if not session or session.get("status") != "OK" or not session.get("file_path"):
            return None
        path = Path(session["file_path"])
        return path if path.is_file() else None

    def qr_path(self, session_id: str) -> Path | None:
        path = self.store.qr_dir / f"{session_id}.png"
        return path if path.is_file() else None

    @staticmethod
    def _check_identity(session_id: str, shot: int, angle: str) -> None:
        if not SESSION_RE.match(session_id):
            raise ValueError("session_id 형식이 올바르지 않습니다. 예: S20261111-142501")
        if int(shot) not in SHOTS:
            raise ValueError("shot은 1 또는 2입니다.")
        if angle not in ANGLES:
            raise ValueError("angle은 front 또는 side입니다.")


def image_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def _optional_int(value):
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value):
    if value is None or value == "":
        return None
    return float(value)


def _optional_bool(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes"}
