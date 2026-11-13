#!/usr/bin/env python3
"""Pretend to be the other nodes so Node 1 can be tested alone.

Plays a visitor and Nodes 2, 3, 4 over MQTT, following docs/MQTT_PROTOCOL.md:
    - (optional) HC-SR04 WAKE_UP                         as Node 3
    - gestures PALM, V_SIGN, FINGERS, THUMBS_UP          as Node 2
    - face_count and gaze during the countdown           as Node 2
    - capture/done for each booth/cmd/capture            as Node 2 (front) and Node 3 (side)
    - compose/result for each booth/compose/request      as Node 4

Usage:
    python tools/fake_nodes.py --host localhost --wake --auto --once
        full automatic session, then exit
    python tools/fake_nodes.py --host localhost
        only answer capture / compose (gestures from the Arduino button or by hand)
Options:
    --people 3          face_count (3+ = 2x2 wide layout)
    --color 2           fingers shown at frame selection (1 white, 2 pink, 3 blue, 4 black)
    --look-away         gaze_ok=false at the first countdown, fixed after 2.5 s
    --drop node3        that camera never answers (tests the error path)
"""

import argparse
import os
import random
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from common.booth_mqtt import T, connect, disconnect, make_payload, parse_payload  # noqa: E402

NODE = "fake"


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  [fake] {msg}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fake Nodes 2/3/4 + visitor for testing Node 1")
    parser.add_argument("--host", default=None)
    parser.add_argument("--wake", action="store_true", help="send WAKE_UP at start")
    parser.add_argument("--auto", action="store_true", help="also play the visitor's gestures")
    parser.add_argument("--once", action="store_true", help="exit after one finished session")
    parser.add_argument("--people", type=int, default=2)
    parser.add_argument("--color", type=int, default=2, choices=[1, 2, 3, 4])
    parser.add_argument("--look-away", action="store_true")
    parser.add_argument("--drop", default="", help="camera node that never answers, e.g. node3")
    args = parser.parse_args()

    finished = threading.Event()
    seen = {"key": None}
    looked_away = {"done": False}
    client_box = {}

    def pub(topic, **fields):
        client_box["c"].publish(topic, make_payload(**fields), qos=1)

    def later(seconds, fn, *a, **kw):
        timer = threading.Timer(seconds, fn, a, kw)
        timer.daemon = True
        timer.start()

    def gesture(name, value=None):
        log(f"visitor shows {name}" + (f" {value}" if value else ""))
        pub(T.GESTURE, session_id=None, gesture=name, value=value, confidence=0.9)

    def on_state(d):
        key = (d.get("state"), d.get("session_id"), d.get("shot"))
        if key == seen["key"]:
            return
        seen["key"] = key
        state, sid = d.get("state"), d.get("session_id")
        if state == "DONE":
            log(f"session finished, QR url = {d.get('url')}")
            if args.once:
                later(0.5, finished.set)
        if state == "ERROR":
            log(f"session error: {d.get('error')}")
            if args.once:
                later(0.5, finished.set)
        if not args.auto:
            return
        if state == "IDLE" and sid is None:
            later(1.0, gesture, "PALM")
        elif state == "IDLE" and sid:
            later(1.0, gesture, "V_SIGN")
        elif state == "COUNTDOWN":
            pub(T.FACES, session_id=sid, face_count=args.people)
            if args.look_away and not looked_away["done"]:
                looked_away["done"] = True
                log("visitor looks at the screen, not the lens")
                pub(T.GAZE, session_id=sid, gaze_ok=False)
                later(5.5, lambda: (log("visitor looks at the lens"),
                                    pub(T.GAZE, session_id=sid, gaze_ok=True)))
            else:
                pub(T.GAZE, session_id=sid, gaze_ok=True)
        elif state == "SELECT":
            later(1.0, gesture, "FINGERS", args.color)
            later(2.0, gesture, "THUMBS_UP")

    def on_capture(d):
        sid, shot = d["session_id"], d["shot"]
        wait = max(0.0, d["capture_at"] / 1000 - time.time())
        for node, angle in (("node2", "front"), ("node3", "side")):
            if node == args.drop:
                log(f"{node} stays silent (--drop)")
                continue
            fields = dict(session_id=sid, shot=shot, node=node, angle=angle,
                          file=f"{sid}_{shot}_{angle}.jpg",
                          faces=[{"x": 400, "y": 180, "w": 160, "h": 160}] * args.people)
            if node == "node2":
                fields.update(face_count=args.people, smile_score=random.randint(55, 95),
                              gaze_ok=True)
            later(wait + 0.2 + random.random() * 0.2,
                  lambda f=fields: (log(f"{f['node']} photo {f['file']}"),
                                    pub(T.CAPTURE_DONE, **f)))

    def on_compose(d):
        sid = d["session_id"]
        layout = "wide" if args.people >= 3 else "vertical"
        later(1.5, lambda: (log(f"node4 composed {layout} / {d['frame_color']}"),
                            pub(T.COMPOSE_RESULT, session_id=sid, status="OK", layout=layout,
                                best_shot=f"{sid}_2_front.jpg",
                                url=f"http://pb-node1.local/r/{sid}", error=None)))

    handlers = {T.SESSION_STATE: on_state, T.CAPTURE_CMD: on_capture,
                T.COMPOSE_REQUEST: on_compose}

    def on_message(client, userdata, msg):
        data = parse_payload(msg.payload)
        if data is not None and msg.topic in handlers:
            handlers[msg.topic](data)

    client = connect(NODE, args.host, on_message=on_message,
                     subscriptions=[(topic, 1) for topic in handlers])
    client_box["c"] = client
    client.loop_start()
    if args.wake:
        later(1.0, lambda: (log("visitor walks up (WAKE_UP 82 cm)"),
                            pub(T.PROXIMITY, event="WAKE_UP", distance_cm=82.0)))
    try:
        while not finished.wait(0.2):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        disconnect(client, NODE)
        client.loop_stop()


if __name__ == "__main__":
    main()
