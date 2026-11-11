"""카메라 노드가 보내 줄 사진 형식과 합성·다운로드 HTTP."""

from __future__ import annotations

import json

from flask import Flask, jsonify, request, send_file

from node4.service import Node4Service


def create_app(service: Node4Service) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

    @app.get("/health")
    def health():
        return jsonify({"node": "node4", "ok": True})

    @app.post("/api/photos")
    def upload_photo():
        """Node 2·3이 사진 파일을 보낸다.

        multipart 필드: file, session_id, shot, angle, node,
        face_count, smile_score, gaze_ok, faces(JSON 배열), filename
        """
        try:
            uploaded = request.files.get("file")
            if uploaded is None:
                raise ValueError("file 필드가 없습니다.")
            data = uploaded.read()
            if not data:
                raise ValueError("빈 파일입니다.")
            faces = _faces(request.form.get("faces"))
            saved = service.add_photo(
                session_id=request.form.get("session_id", ""),
                shot=int(request.form.get("shot", "0")),
                angle=request.form.get("angle", ""),
                data=data,
                node=request.form.get("node") or None,
                filename=request.form.get("filename") or None,
                face_count=_optional_int(request.form.get("face_count")),
                smile_score=_optional_float(request.form.get("smile_score")),
                gaze_ok=_optional_bool(request.form.get("gaze_ok")),
                faces=faces,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({
            "ok": True,
            "session_id": saved["session_id"],
            "shot": saved["shot"],
            "angle": saved["angle"],
            "file": saved["filename"],
        })

    @app.post("/api/compose")
    def compose():
        """합성 요청. MQTT booth/compose/request와 같은 역할의 PC용 입구."""
        body = request.get_json(silent=True) or {}
        try:
            payload = service.compose(
                session_id=str(body.get("session_id", "")),
                frame_color=str(body.get("frame_color") or "white"),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(payload)

    @app.get("/download/<session_id>")
    def download_page(session_id: str):
        session = service.store.get_session(session_id)
        if session is None or session.get("status") != "OK":
            return _page("결과를 찾을 수 없습니다", "<p>합성이 끝나지 않았거나 사진이 빠졌습니다.</p>"), 404
        layout = "세로 4컷" if session.get("layout") == "vertical" else "2×2 와이드"
        body = f"""
        <p class="meta">{session_id} · {layout} · {session.get("frame_color")}</p>
        <img class="photo" src="/results/{session_id}.jpg" alt="네컷 결과">
        <a class="btn" href="/results/{session_id}.jpg" download>이미지 저장</a>
        """
        return _page("촬영 결과", body)

    @app.get("/results/<session_id>.jpg")
    def result_file(session_id: str):
        path = service.result_path(session_id)
        if path is None:
            return jsonify({"ok": False, "error": "결과 이미지가 없습니다."}), 404
        return send_file(path, mimetype="image/jpeg")

    @app.get("/qr/<session_id>.png")
    def qr_file(session_id: str):
        path = service.qr_path(session_id)
        if path is None:
            return jsonify({"ok": False, "error": "QR이 없습니다."}), 404
        return send_file(path, mimetype="image/png")

    return app


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ margin: 0; background: #f4f1ee; color: #1d1d1f;
           font-family: "Malgun Gothic", sans-serif; }}
    main {{ max-width: 460px; margin: 28px auto 48px; padding: 0 16px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    .meta {{ color: #666; margin: 0 0 16px; }}
    .photo {{ width: 100%; border-radius: 16px; background: white; }}
    .btn {{ display: block; margin-top: 16px; background: #161616; color: white;
           text-align: center; text-decoration: none; padding: 14px 16px;
           border-radius: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    {body}
  </main>
</body>
</html>"""


def _faces(raw: str | None):
    if not raw:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("faces는 JSON 배열이어야 합니다.")
    return parsed


def _optional_int(value: str | None):
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: str | None):
    if value is None or value == "":
        return None
    return float(value)


def _optional_bool(value: str | None):
    if value is None or value == "":
        return None
    return value.lower() in {"1", "true", "yes"}
