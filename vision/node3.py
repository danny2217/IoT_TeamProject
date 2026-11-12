"""Node 3 — 보조 각도 카메라: 촬영 명령 처리, LCD 상태 표시.

    python -m vision.node3 --host 172.20.10.4 --show
    python -m vision.node3 --node4 http://pb-node4.local:5000 --show --with-proximity

구독: booth/session/state, booth/cmd/capture
발행: booth/status/node3, booth/capture/done (angle=side, 얼굴 분석은 Node 4가 한다)

--with-proximity: 막심 님의 node3_sensor/proximity.py 판정 로직을 이 프로세스 안에서 돌린다.
proximity.py를 따로 실행하면 같은 "node3" 클라이언트 ID가 둘이 되어 브로커가 한쪽을
계속 끊으므로, Node 3에서는 둘 중 한 가지 방식만 쓴다.
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import cv2

from common.booth_mqtt import T, connect, disconnect, make_payload, parse_payload
from vision.camera import open_camera, schedule_capture

NODE = "node3"


def start_proximity(client, status: dict, trigger: int, echo: int) -> None:
    """HC-SR04를 읽어 WAKE_UP / LEAVE 를 발행하는 스레드."""
    from gpiozero import DistanceSensor
    from node3_sensor.proximity import ProximityDetector

    sensor = DistanceSensor(echo=echo, trigger=trigger, max_distance=2.0, queue_len=3)
    detector = ProximityDetector()

    def run() -> None:
        while True:
            distance_cm = sensor.distance * 100.0
            status["distance_cm"] = distance_cm
            event = detector.update(distance_cm, time.monotonic())
            if event:
                name, median = event
                client.publish(T.PROXIMITY,
                               make_payload(event=name, distance_cm=round(median, 1)), qos=1)
                print(f"[{NODE}] {name} ({median:.1f} cm)")
            time.sleep(0.1)

    threading.Thread(target=run, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Node 3 side camera")
    parser.add_argument("--host", default=None, help="MQTT 브로커 (기본 pb-node1.local)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--camera", default="0", help="USB 웹캠 번호 또는 pi(카메라 모듈)")
    parser.add_argument("--width", type=int, default=1280, help="촬영 해상도")
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--out", default="data/captures")
    parser.add_argument("--node4", default=None, help="Node 4 주소, 예: http://pb-node4.local:5000")
    parser.add_argument("--show", action="store_true", help="LCD에 미리보기와 상태 표시")
    parser.add_argument("--lcd", default="800x480", help="LCD 해상도 (가로)")
    parser.add_argument("--with-proximity", action="store_true", help="HC-SR04도 같이 돌린다")
    parser.add_argument("--trigger", type=int, default=23)
    parser.add_argument("--echo", type=int, default=24)
    args = parser.parse_args()

    camera = open_camera(args.camera, args.width, args.height)
    out_dir = Path(args.out)
    status = {"state": None, "session_id": None, "last_shot": "-", "distance_cm": None,
              "connected": False}

    def on_message(c, userdata, msg):
        data = parse_payload(msg.payload)
        if not isinstance(data, dict):
            return
        if msg.topic == T.SESSION_STATE:
            status["state"] = data.get("state")
            status["session_id"] = data.get("session_id")
        elif msg.topic == T.CAPTURE_CMD:
            status["last_shot"] = f"shot {data.get('shot')}"
            schedule_capture(camera, data, node=NODE, angle="side", out_dir=out_dir,
                             client=c, node4_url=args.node4)

    client = connect(NODE, args.host, args.port, on_message=on_message,
                     subscriptions=[(T.SESSION_STATE, 1), (T.CAPTURE_CMD, 1)])
    client.loop_start()
    if args.with_proximity:
        start_proximity(client, status, args.trigger, args.echo)

    lcd_w, lcd_h = (int(v) for v in args.lcd.lower().split("x"))
    if args.show:
        cv2.namedWindow("node3", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("node3", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    print(f"[{NODE}] 카메라 {camera.width}x{camera.height} 준비 (Ctrl+C 종료)")
    try:
        while True:
            if not args.show:
                time.sleep(0.5)
                continue
            _, frame = camera.read()
            if frame is None:
                time.sleep(0.05)
                continue
            view = cv2.resize(frame, (lcd_w, lcd_h))
            online = client.is_connected()
            lines = [f"MQTT {'online' if online else 'OFFLINE'}  {status['state'] or '-'}",
                     f"last: {status['last_shot']}"]
            if status["distance_cm"] is not None:
                lines.append(f"dist: {status['distance_cm']:.0f} cm")
            for i, text in enumerate(lines):
                cv2.putText(view, text, (8, 22 + i * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 255, 0) if online else (0, 0, 255), 2)
            cv2.imshow("node3", view)
            if cv2.waitKey(100) & 0xFF == ord("q"):  # LCD 갱신은 초당 10번이면 충분하다
                break
    except KeyboardInterrupt:
        pass
    finally:
        disconnect(client, NODE)
        client.loop_stop()
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
