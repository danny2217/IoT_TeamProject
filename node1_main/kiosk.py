#!/usr/bin/env python3
"""Node 1 kiosk screen: a web page that follows the booth session live.

Listens to MQTT and pushes every change to the browser (Server-Sent Events):
    booth/session/state   which screen to show (countdown, frame choice, QR ...)
    booth/status/+        node online/offline bar at the bottom
    booth/vision/faces    number of people in front of the camera

Only the Python standard library plus paho-mqtt; `qrcode` is optional
(without it the DONE screen shows the link as text).

Usage (on the RPi 400, next to session_manager.py):
    python node1_main/kiosk.py --host localhost            # http://<this pi>:8000
    chromium-browser --kiosk --noerrdialogs http://localhost:8000
"""

import argparse
import io
import json
import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from common.booth_mqtt import T, connect, disconnect, parse_payload  # noqa: E402

NODE = "kiosk"
PAGE = os.path.join(HERE, "kiosk.html")

try:
    import qrcode
    import qrcode.image.svg
except ImportError:  # optional
    qrcode = None


class Hub:
    """Latest booth snapshot + the browsers that listen to it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.snapshot = {"session": {"state": "SLEEP", "session_id": None, "shot": 0},
                         "nodes": {}, "faces": None, "broker": False}
        self.clients = []

    def update(self, fn):
        with self.lock:
            fn(self.snapshot)
            message = json.dumps(self.snapshot, ensure_ascii=False)
            for client in list(self.clients):
                client.put(message)

    def subscribe(self):
        client = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.append(client)
            client.put(json.dumps(self.snapshot, ensure_ascii=False))
        return client

    def unsubscribe(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)


HUB = Hub()


def qr_svg(text: str) -> bytes:
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/state":
            with HUB.lock:
                body = json.dumps(HUB.snapshot, ensure_ascii=False).encode()
            self._send(200, body, "application/json; charset=utf-8")
        elif url.path == "/qr.svg":
            text = parse_qs(url.query).get("text", [""])[0]
            if qrcode is None or not text:
                self._send(404, b"qrcode not installed", "text/plain")
            else:
                self._send(200, qr_svg(text), "image/svg+xml")
        elif url.path == "/events":
            self._events()
        else:
            self._send(404, b"not found", "text/plain")

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        client = HUB.subscribe()
        try:
            while True:
                try:
                    message = client.get(timeout=15)
                    self.wfile.write(f"data: {message}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")      # detects closed browsers
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(client)


def main():
    parser = argparse.ArgumentParser(description="Smart Photo Booth kiosk screen (Node 1)")
    parser.add_argument("--host", default=None, help="MQTT broker (default pb-node1.local)")
    parser.add_argument("--port", type=int, default=8000, help="web port")
    args = parser.parse_args()

    def on_message(client, userdata, msg):
        data = parse_payload(msg.payload)
        if data is None:
            return
        if msg.topic == T.SESSION_STATE:
            HUB.update(lambda s: s.__setitem__("session", data))
        elif msg.topic.startswith("booth/status/"):
            node = data.get("node") or msg.topic.rsplit("/", 1)[-1]
            if node in ("monitor", "kiosk", "fake"):
                return
            HUB.update(lambda s: s["nodes"].__setitem__(node, data.get("state")))
        elif msg.topic == T.FACES:
            HUB.update(lambda s: s.__setitem__("faces", data.get("face_count")))

    client = connect(NODE, args.host, on_message=on_message,
                     subscriptions=[(T.SESSION_STATE, 1), ("booth/status/+", 1), (T.FACES, 0)])
    original_on_connect = client.on_connect
    original_on_disconnect = client.on_disconnect

    def on_connect(c, userdata, flags, reason_code, properties):
        original_on_connect(c, userdata, flags, reason_code, properties)
        HUB.update(lambda s: s.__setitem__("broker", not reason_code.is_failure))

    def on_disconnect(c, userdata, flags, reason_code, properties):
        original_on_disconnect(c, userdata, flags, reason_code, properties)
        HUB.update(lambda s: s.__setitem__("broker", False))

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.loop_start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    print(f"kiosk on http://0.0.0.0:{args.port}  (QR: {'on' if qrcode else 'off, pip install qrcode'})",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        disconnect(client, NODE)
        client.loop_stop()


if __name__ == "__main__":
    main()
