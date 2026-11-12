"""카메라(USB 웹캠·카메라 모듈) 읽기와 예약 시각 촬영. Node 2·3이 같이 쓴다."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import cv2

from common.booth_mqtt import T, make_payload, now_ms


class Camera:
    """별도 스레드에서 계속 읽어 항상 가장 최근 프레임을 준다(버퍼 지연 방지)."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        self._cap = cv2.VideoCapture(index, backend)
        if not self._cap.isOpened():
            raise RuntimeError(f"카메라 {index}번을 열 수 없습니다. USB 연결과 /dev/video{index}를 확인하세요.")
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            with self._lock:
                self._frame = frame
                self._seq += 1

    def read(self):
        """(프레임 번호, 프레임). 아직 없으면 (0, None). 프레임은 수정하지 말고 쓴다."""
        with self._lock:
            return self._seq, self._frame

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=1)
        self._cap.release()


class PiCamera(Camera):
    """라즈베리 파이 카메라 모듈(CSI). OpenCV로는 열리지 않아 Picamera2로 읽는다."""

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30):
        from picamera2 import Picamera2

        self._cam = Picamera2()
        # Picamera2의 "RGB888"은 배열 기준 BGR 순서라 OpenCV에 그대로 쓸 수 있다.
        self._cam.configure(self._cam.create_video_configuration(
            main={"size": (width, height), "format": "RGB888"},
            controls={"FrameRate": fps}))
        self._cam.start()
        self.width, self.height = width, height
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            frame = self._cam.capture_array()
            with self._lock:
                self._frame = frame
                self._seq += 1

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=1)
        self._cam.stop()
        self._cam.close()


def open_camera(source: str = "0", width: int = 1280, height: int = 720) -> Camera:
    """source: USB 웹캠 번호("0", "1", ...) 또는 카메라 모듈("pi")."""
    if str(source).lower() == "pi":
        return PiCamera(width, height)
    return Camera(int(source), width, height)


def capture_filename(session_id: str, shot: int, angle: str) -> str:
    return f"{session_id}_{shot}_{angle}.jpg"


def schedule_capture(camera: Camera, cmd: dict, *, node: str, angle: str, out_dir: Path,
                     client=None, analyze=None, node4_url: str | None = None) -> threading.Thread:
    """booth/cmd/capture 한 건을 처리하는 스레드를 띄운다.

    capture_at 까지 기다렸다가 촬영 → 저장 → (node4_url 이 있으면) HTTP 전송 →
    booth/capture/done 발행. analyze(frame)는 payload에 넣을 분석 결과 dict를 돌려준다.
    """

    def run() -> None:
        session_id = str(cmd.get("session_id") or "")
        shot = int(cmd.get("shot") or 0)
        capture_at = cmd.get("capture_at") or now_ms()
        wait_s = (capture_at - now_ms()) / 1000.0
        if wait_s > 0:
            time.sleep(wait_s)
        _, frame = camera.read()
        shot_ts = now_ms()
        if frame is None:
            print(f"[{node}] 촬영 실패: 카메라 프레임이 없습니다.")
            return
        frame = frame.copy()
        late_ms = shot_ts - capture_at
        filename = capture_filename(session_id, shot, angle)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f"[{node}] 촬영 {filename} (예약 시각 대비 {late_ms:+d} ms)")

        info = analyze(frame) if analyze else {}
        fields = dict(session_id=session_id, shot=shot, node=node, angle=angle,
                      file=filename, **info, ts=shot_ts)

        if node4_url:
            try:
                upload_photo(node4_url, path, fields)
            except Exception as exc:
                print(f"[{node}] Node 4 전송 실패: {exc}")
                return
        if client is not None:
            client.publish(T.CAPTURE_DONE, make_payload(**fields), qos=1)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def upload_photo(node4_url: str, path: Path, fields: dict) -> None:
    """Node 4의 POST /api/photos (multipart) 로 사진과 분석 결과를 보낸다."""
    import requests

    form = {"session_id": fields["session_id"], "shot": str(fields["shot"]),
            "angle": fields["angle"], "node": fields["node"], "filename": fields["file"]}
    for key in ("face_count", "smile_score"):
        if fields.get(key) is not None:
            form[key] = str(fields[key])
    if fields.get("gaze_ok") is not None:
        form["gaze_ok"] = "true" if fields["gaze_ok"] else "false"
    if fields.get("faces") is not None:
        form["faces"] = json.dumps(fields["faces"])
    with open(path, "rb") as handle:
        response = requests.post(node4_url.rstrip("/") + "/api/photos", data=form,
                                 files={"file": (fields["file"], handle, "image/jpeg")},
                                 timeout=10)
    response.raise_for_status()
