#!/usr/bin/env python3
"""Print every booth/# MQTT message with time and pretty JSON.

Usage:
    python node1_main/mqtt_monitor.py                 # broker = pb-node1.local
    python node1_main/mqtt_monitor.py --host localhost
    python node1_main/mqtt_monitor.py --topic booth/sensor/#
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from common.booth_mqtt import BROKER_HOST, BROKER_PORT, T, connect, disconnect, parse_payload  # noqa: E402

NODE = "monitor"


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Photo Booth MQTT monitor")
    parser.add_argument("--host", default=BROKER_HOST)
    parser.add_argument("--port", type=int, default=BROKER_PORT)
    parser.add_argument("--topic", default=T.ALL)
    args = parser.parse_args()

    def on_message(client, userdata, msg):
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        retained = " (retained)" if msg.retain else ""
        data = parse_payload(msg.payload)
        if data is None:
            body = f"!! not JSON: {msg.payload!r}"
        else:
            body = json.dumps(data, ensure_ascii=False)
        print(f"{stamp}  {msg.topic}{retained}\n          {body}", flush=True)

    client = connect(NODE, args.host, args.port, on_message=on_message,
                     subscriptions=[(args.topic, 1)])
    print(f"Listening on {args.host}:{args.port} topic '{args.topic}' (Ctrl+C to stop)")
    client.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        disconnect(client, NODE)
        client.loop_stop()


if __name__ == "__main__":
    main()
