"""모니터 없는 라즈베리 파이용 미리보기: 브라우저에서 http://<호스트>:<포트>/ 로 본다 (MJPEG)."""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

PAGE = (b"<!doctype html><title>%s</title>"
        b"<body style='margin:0;background:#111;display:grid;place-items:center;height:100vh'>"
        b"<img src='/stream' style='max-width:100%%;max-height:100vh'>")


class _Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6      # "::" 에 묶으면 IPv4·IPv6 둘 다 받는다
    daemon_threads = True


class WebPreview:
    """update(frame)로 넘긴 최신 화면을 접속한 브라우저에 계속 보낸다."""

    def __init__(self, port: int, title: str = "preview", scale: int = 1):
        self._jpeg = None
        self._seq = 0
        self._cond = threading.Condition()
        self._scale = scale
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # 접속 로그는 찍지 않는다
                pass

            def do_GET(self):
                if self.path != "/stream":
                    body = PAGE % title.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                last = 0
                try:
                    while True:
                        with preview._cond:
                            preview._cond.wait_for(lambda: preview._seq != last, timeout=5)
                            last, jpeg = preview._seq, preview._jpeg
                        if jpeg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = _Server(("::", port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._last = 0.0
        print(f"[web] 미리보기: http://{socket.gethostname()}.local:{port}/")

    def update(self, frame, max_fps: float = 15.0) -> None:
        now = time.monotonic()
        if now - self._last < 1.0 / max_fps:
            return
        self._last = now
        if self._scale != 1:
            frame = cv2.resize(frame, None, fx=self._scale, fy=self._scale,
                               interpolation=cv2.INTER_NEAREST)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            with self._cond:
                self._jpeg, self._seq = buf.tobytes(), self._seq + 1
                self._cond.notify_all()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
