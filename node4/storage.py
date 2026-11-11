"""세션·컷 저장. 기본은 SQLite와 로컬 폴더이고, 버킷이 있으면 S3에도 올린다."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.upload_dir = self.root / "uploads"
        self.result_dir = self.root / "results"
        self.qr_dir = self.root / "qr"
        for folder in (self.upload_dir, self.result_dir, self.qr_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "booth.db"
        self.lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _db(self):
        with self.lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_db(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.lock:
            conn = self._connect()
            try:
                conn.executescript(schema)
            finally:
                conn.close()

    def upsert_shot(
        self,
        *,
        session_id: str,
        shot: int,
        angle: str,
        node: str | None = None,
        filename: str | None = None,
        file_path: str | None = None,
        face_count: int | None = None,
        smile_score: float | None = None,
        gaze_ok: bool | None = None,
        faces: list | None = None,
    ) -> dict:
        with self._db() as conn:
            current = conn.execute(
                "SELECT * FROM shots WHERE session_id=? AND shot=? AND angle=?",
                (session_id, shot, angle),
            ).fetchone()
            merged = dict(current) if current else {
                "session_id": session_id,
                "shot": shot,
                "angle": angle,
                "node": None,
                "filename": None,
                "file_path": None,
                "face_count": None,
                "smile_score": None,
                "gaze_ok": None,
                "faces_json": None,
            }
            if node is not None:
                merged["node"] = node
            if filename is not None:
                merged["filename"] = filename
            if file_path is not None:
                merged["file_path"] = file_path
            if face_count is not None:
                merged["face_count"] = int(face_count)
            if smile_score is not None:
                merged["smile_score"] = float(smile_score)
            if gaze_ok is not None:
                merged["gaze_ok"] = 1 if gaze_ok else 0
            if faces is not None:
                merged["faces_json"] = json.dumps(faces, ensure_ascii=False)
            merged["updated_at"] = utc_now()
            conn.execute(
                """
                INSERT INTO shots (
                    session_id, shot, angle, node, filename, file_path,
                    face_count, smile_score, gaze_ok, faces_json, updated_at
                ) VALUES (
                    :session_id, :shot, :angle, :node, :filename, :file_path,
                    :face_count, :smile_score, :gaze_ok, :faces_json, :updated_at
                )
                ON CONFLICT(session_id, shot, angle) DO UPDATE SET
                    node=excluded.node,
                    filename=excluded.filename,
                    file_path=excluded.file_path,
                    face_count=excluded.face_count,
                    smile_score=excluded.smile_score,
                    gaze_ok=excluded.gaze_ok,
                    faces_json=excluded.faces_json,
                    updated_at=excluded.updated_at
                """,
                merged,
            )
        return self.get_shot(session_id, shot, angle)

    def get_shot(self, session_id: str, shot: int, angle: str) -> dict | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM shots WHERE session_id=? AND shot=? AND angle=?",
                (session_id, shot, angle),
            ).fetchone()
        return _shot_dict(row) if row else None

    def list_shots(self, session_id: str) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM shots WHERE session_id=? ORDER BY shot, angle",
                (session_id,),
            ).fetchall()
        return [_shot_dict(row) for row in rows]

    def save_session(self, record: dict) -> None:
        record = dict(record)
        record.setdefault("created_at", utc_now())
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, layout, frame_color, face_count, best_shot, shot_scores,
                    file_path, download_url, s3_url, storage, status, error, created_at
                ) VALUES (
                    :session_id, :layout, :frame_color, :face_count, :best_shot, :shot_scores,
                    :file_path, :download_url, :s3_url, :storage, :status, :error, :created_at
                )
                ON CONFLICT(session_id) DO UPDATE SET
                    layout=excluded.layout,
                    frame_color=excluded.frame_color,
                    face_count=excluded.face_count,
                    best_shot=excluded.best_shot,
                    shot_scores=excluded.shot_scores,
                    file_path=excluded.file_path,
                    download_url=excluded.download_url,
                    s3_url=excluded.s3_url,
                    storage=excluded.storage,
                    status=excluded.status,
                    error=excluded.error,
                    created_at=excluded.created_at
                """,
                {
                    "layout": None,
                    "frame_color": None,
                    "face_count": None,
                    "best_shot": None,
                    "shot_scores": None,
                    "file_path": None,
                    "download_url": None,
                    "s3_url": None,
                    "storage": None,
                    "error": None,
                    **record,
                },
            )

    def get_session(self, session_id: str) -> dict | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        if data.get("shot_scores"):
            data["shot_scores"] = json.loads(data["shot_scores"])
        return data


def _shot_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    if data.get("faces_json"):
        data["faces"] = json.loads(data["faces_json"])
    else:
        data["faces"] = []
    if data.get("gaze_ok") is None:
        data["gaze_ok"] = None
    else:
        data["gaze_ok"] = bool(data["gaze_ok"])
    return data


def upload_result_to_s3(local_path: Path, key: str) -> str | None:
    """AWS_S3_BUCKET이 있으면 업로드하고 URL을 돌려준다. 실패하면 None."""
    bucket = os.environ.get("AWS_S3_BUCKET", "").strip()
    if not bucket:
        return None
    try:
        import boto3
    except ImportError:
        print("[node4] boto3가 없어 S3 업로드를 건너뜁니다.")
        return None
    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    try:
        client = boto3.client("s3", region_name=region)
        client.upload_file(
            str(local_path),
            bucket,
            key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
    except Exception as exc:
        print(f"[node4] S3 업로드 실패, 로컬 저장을 사용합니다: {exc}")
        return None
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
