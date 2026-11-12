"""Node 2 — 정면 카메라: 제스처 · 인원수 발행, 촬영 명령 처리.

    python -m vision.node2 --dry-run --show         # 브로커 없이 화면으로 인식 확인
    python -m vision.node2 --dry-run --web 8080     # 모니터 없이: 브라우저에서 http://raspi.local:8080
    python -m vision.node2 --host 172.20.10.4       # 브로커에 발행
    python -m vision.node2 --node4 http://pb-node4.local:5000   # 촬영 사진을 Node 4로 전송

구독: booth/session/state, booth/cmd/capture
발행: booth/status/node2, booth/vision/gesture, booth/vision/faces, booth/capture/done
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import cv2

from common.booth_mqtt import T, connect, disconnect, make_payload, parse_payload
from vision.camera import open_camera, schedule_capture
from vision.faces import CountDebouncer, FaceDetector
from vision.gestures import ALLOWED, GestureStabilizer, classify
from vision.hands import create_hand_tracker

NODE = "node2"
PROCESS_SIZE = (320, 240)                    # 인식은 저해상도, 촬영은 원본 해상도
FACE_STATES = {None, "IDLE", "COUNTDOWN"}    # 실시간 인원수를 세는 상태
FACE_INTERVAL_S = 0.3


class Session:
    """MQTT 스레드가 쓰고 메인 루프가 읽는 세션 상태."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = None
        self._session_id = None

    def set(self, state, session_id) -> None:
        with self._lock:
            self._state, self._session_id = state, session_id

    def get(self):
        with self._lock:
            return self._state, self._session_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Node 2 front camera vision")
    parser.add_argument("--host", default=None, help="MQTT 브로커 (기본 pb-node1.local)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--camera", default="0", help="USB 웹캠 번호 또는 pi(카메라 모듈)")
    parser.add_argument("--width", type=int, default=1280, help="촬영 해상도")
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--hands", default="auto", choices=["auto", "solutions", "tasks", "opencv"])
    parser.add_argument("--hold", type=float, default=0.5, help="제스처 유지 시간(초)")
    parser.add_argument("--out", default="data/captures", help="촬영 사진 저장 폴더")
    parser.add_argument("--node4", default=None, help="Node 4 주소, 예: http://pb-node4.local:5000")
    parser.add_argument("--show", action="store_true", help="미리보기 창 표시")
    parser.add_argument("--web", type=int, default=None, metavar="PORT",
                        help="모니터가 없을 때: 브라우저로 보는 미리보기 포트 (예: 8080)")
    parser.add_argument("--dry-run", action="store_true", help="MQTT 없이 인식 결과만 출력")
    args = parser.parse_args()

    camera = open_camera(args.camera, args.width, args.height)
    hands = create_hand_tracker(args.hands)
    face_detector = FaceDetector()
    still_detector = FaceDetector()  # 촬영 스레드용
    print(f"[{NODE}] 카메라 {camera.width}x{camera.height}, 얼굴 검출 {face_detector.name}")

    web = None
    if args.web:
        from vision.webview import WebPreview
        web = WebPreview(args.web, title="node2", scale=2)

    session = Session()
    out_dir = Path(args.out)
    client = None

    def analyze(frame) -> dict:
        faces = still_detector.detect(frame, max_width=640)
        return {"face_count": len(faces), "faces": faces}

    def on_message(c, userdata, msg):
        data = parse_payload(msg.payload)
        if not isinstance(data, dict):
            return
        if msg.topic == T.SESSION_STATE:
            session.set(data.get("state"), data.get("session_id"))
        elif msg.topic == T.CAPTURE_CMD:
            schedule_capture(camera, data, node=NODE, angle="front", out_dir=out_dir,
                             client=c, analyze=analyze, node4_url=args.node4)

    if not args.dry_run:
        client = connect(NODE, args.host, args.port, on_message=on_message,
                         subscriptions=[(T.SESSION_STATE, 1), (T.CAPTURE_CMD, 1)])
        client.loop_start()

    def publish(topic: str, **fields) -> None:
        print(f"[{NODE}] {topic} {fields}")
        if client is not None:
            client.publish(topic, make_payload(**fields), qos=0)

    stabilizer = GestureStabilizer(hold_s=args.hold)
    face_count = CountDebouncer()
    last_faces: list[dict] = []
    last_seq = 0
    last_face_time = 0.0
    last_state = None
    fps, fps_time, fps_frames = 0.0, time.monotonic(), 0
    label = ""

    try:
        while True:
            seq, frame = camera.read()
            if frame is None or seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = seq
            now = time.monotonic()
            state, session_id = session.get()
            if state != last_state:
                stabilizer.reset()
                last_state = state
            small = cv2.resize(frame, PROCESS_SIZE)

            # 인원수: 상태가 맞을 때만, 0.3초에 한 번
            if state in FACE_STATES and now - last_face_time >= FACE_INTERVAL_S:
                last_face_time = now
                last_faces = face_detector.detect(small)
                changed = face_count.update(len(last_faces))
                if changed is not None:
                    publish(T.FACES, session_id=session_id, face_count=changed)

            # 제스처: 이 상태에서 받을 제스처가 있을 때만 손 모델을 돌린다
            confirmed = None
            if ALLOWED.get(state):
                exclude = [(f["x"], f["y"], f["w"], f["h"]) for f in last_faces]
                found = hands.detect(small, exclude=exclude)
                gesture = classify(found[0], state) if found else None
                confirmed = stabilizer.update(gesture, now)
                if confirmed:
                    name, value = confirmed
                    label = name if value is None else f"{name} {value}"
                    publish(T.GESTURE, session_id=session_id, gesture=name, value=value,
                            confidence=round(found[1], 2))
            else:
                stabilizer.update(None, now)

            fps_frames += 1
            if now - fps_time >= 1.0:
                fps = fps_frames / (now - fps_time)
                fps_time, fps_frames = now, 0

            if args.show or web:
                view = small.copy()
                for f in last_faces:
                    cv2.rectangle(view, (f["x"], f["y"]), (f["x"] + f["w"], f["y"] + f["h"]),
                                  (0, 255, 255), 1)
                cv2.putText(view, f"{state or '-'} {fps:.0f}fps faces:{len(last_faces)}",
                            (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
                cv2.putText(view, label, (6, 232), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
                if web:
                    web.update(view)
                if args.show:
                    cv2.imshow("node2", view)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    except KeyboardInterrupt:
        pass
    finally:
        if client is not None:
            disconnect(client, NODE)
            client.loop_stop()
        if web:
            web.close()
        hands.close()
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
