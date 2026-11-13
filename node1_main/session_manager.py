#!/usr/bin/env python3
"""Node 1 session manager: MQTT + Arduino around the session state machine.

    MQTT in  : booth/sensor/proximity, booth/vision/{gesture,faces,gaze},
               booth/capture/done, booth/compose/result
    MQTT out : booth/session/state (retained), booth/cmd/capture, booth/compose/request
    Serial   : Arduino Uno (COUNT:3, SHUTTER, ... / BTN:START, READY)

All events go through one queue and are handled in the main thread,
so the state machine never runs from two threads at once.

Usage:
    python node1_main/session_manager.py                    # broker pb-node1.local, Arduino auto-detect
    python node1_main/session_manager.py --host localhost   # running on the RPi 400 itself
    python node1_main/session_manager.py --no-arduino       # print Arduino commands instead
    python node1_main/session_manager.py --cameras node2    # only one camera node connected
"""

import argparse
import glob
import os
import queue
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from common.booth_mqtt import T, connect, disconnect, make_payload, parse_payload  # noqa: E402
from session_machine import Config, SessionMachine  # noqa: E402

NODE = "node1"
BAUD = 115200


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {msg}", flush=True)


class ArduinoLink(threading.Thread):
    """Reads lines from the Arduino into the event queue; reconnects if unplugged."""

    def __init__(self, events: queue.Queue, port: str = None):
        super().__init__(daemon=True)
        self.events = events
        self.port = port
        self.ser = None
        self.lock = threading.Lock()

    def _find_port(self):
        if self.port:
            return self.port
        ports = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
        return ports[0] if ports else None

    def run(self) -> None:
        import serial  # pyserial
        while True:
            if self.ser is None:
                port = self._find_port()
                if port is None:
                    time.sleep(3)
                    continue
                try:
                    with self.lock:
                        self.ser = serial.Serial(port, BAUD, timeout=0.2)
                    log(f"Arduino connected on {port}")
                except serial.SerialException as exc:
                    log(f"Arduino open failed ({exc}), retrying")
                    time.sleep(3)
                    continue
            try:
                line = self.ser.readline().decode("utf-8", errors="replace").strip()
            except (serial.SerialException, OSError) as exc:
                log(f"Arduino disconnected ({exc})")
                with self.lock:
                    self.ser = None
                continue
            if line:
                self.events.put(("arduino_line", line))

    def send(self, cmd: str) -> None:
        with self.lock:
            if self.ser is None:
                log(f"   (Arduino not connected, dropped {cmd})")
                return
            try:
                self.ser.write((cmd + "\n").encode("ascii", errors="replace"))
            except Exception as exc:  # noqa: BLE001 - keep the booth running
                log(f"   Arduino write failed: {exc}")


TOPIC_TO_EVENT = {
    T.PROXIMITY: lambda d: {"type": "proximity", "event": d.get("event")},
    T.GESTURE: lambda d: {"type": "gesture", "gesture": d.get("gesture"), "value": d.get("value")},
    T.FACES: lambda d: {"type": "faces", "face_count": d.get("face_count")},
    T.GAZE: lambda d: {"type": "gaze", "gaze_ok": d.get("gaze_ok")},
    T.CAPTURE_DONE: lambda d: dict(d, type="capture_done"),
    T.COMPOSE_RESULT: lambda d: dict(d, type="compose_result"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Photo Booth Node 1 session manager")
    parser.add_argument("--host", default=None, help="MQTT broker (default pb-node1.local)")
    parser.add_argument("--port", help="Arduino serial port (default: auto-detect)")
    parser.add_argument("--no-arduino", action="store_true", help="print Arduino commands only")
    parser.add_argument("--cameras", default="node2,node3",
                        help="camera nodes that must report capture/done (comma separated)")
    parser.add_argument("--shots", type=int, default=2)
    args = parser.parse_args()

    events: queue.Queue = queue.Queue()
    cfg = Config(shots=args.shots,
                 expected_cameras=tuple(c.strip() for c in args.cameras.split(",") if c.strip()))
    machine = SessionMachine(cfg)

    arduino = None
    if not args.no_arduino:
        arduino = ArduinoLink(events, args.port)
        arduino.start()

    def on_message(client, userdata, msg):
        data = parse_payload(msg.payload)
        if data is None or msg.topic not in TOPIC_TO_EVENT:
            log(f"!! ignored {msg.topic}: {msg.payload[:80]!r}")
            return
        events.put(("event", TOPIC_TO_EVENT[msg.topic](data)))

    subscriptions = [(topic, 1) for topic in TOPIC_TO_EVENT]
    client = connect(NODE, args.host, on_message=on_message, subscriptions=subscriptions)
    client.loop_start()
    log(f"session manager started (cameras: {', '.join(cfg.expected_cameras)})")

    def flush() -> None:
        for kind, data in machine.drain():
            if kind == "state":
                client.publish(T.SESSION_STATE, make_payload(**data), qos=1, retain=True)
                extra = {k: v for k, v in data.items() if k not in ("state", "session_id")}
                log(f"STATE {data['state']:<9} session={data['session_id']} {extra}")
            elif kind == "arduino":
                log(f"   -> Arduino {data}")
                if arduino is not None:
                    arduino.send(data)
            elif kind == "capture":
                client.publish(T.CAPTURE_CMD, make_payload(**data), qos=1)
                log(f"   -> capture shot {data['shot']} at {data['capture_at']}")
            elif kind == "compose":
                client.publish(T.COMPOSE_REQUEST, make_payload(**data), qos=1)
                log(f"   -> compose request frame={data['frame_color']}")

    try:
        while True:
            try:
                source, item = events.get(timeout=0.05)
            except queue.Empty:
                source = None
            if source == "event":
                kind = item["type"]
                if kind == "capture_done":
                    log(f"<- capture/done {item.get('node')} shot {item.get('shot')}")
                elif kind == "compose_result":
                    log(f"<- compose/result {item.get('status')} {item.get('url') or item.get('error')}")
                elif kind == "gesture":
                    log(f"<- gesture {item.get('gesture')} {item.get('value') or ''}".rstrip())
                elif kind == "proximity":
                    log(f"<- proximity {item.get('event')}")
                elif kind == "faces":
                    log(f"<- faces {item.get('face_count')}")
                elif kind == "gaze":
                    log(f"<- gaze_ok {item.get('gaze_ok')}")
                machine.handle(item)
            elif source == "arduino_line":
                log(f"<- Arduino {item}")
                if item == "BTN:START":
                    machine.handle({"type": "button"})
                elif item == "READY" and machine.last_screen_cmd and arduino is not None:
                    arduino.send(machine.last_screen_cmd)   # Arduino rebooted: restore screen
            machine.tick()
            flush()
    except KeyboardInterrupt:
        pass
    finally:
        disconnect(client, NODE)
        client.loop_stop()


if __name__ == "__main__":
    main()
