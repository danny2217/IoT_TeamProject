"""Shared MQTT helpers for every node of the Smart Photo Booth.

Topic names and payload rules are defined in docs/MQTT_PROTOCOL.md.
Always use the constants in `T` instead of typing topic strings by hand.

Requires paho-mqtt >= 2.0.
"""

import json
import os
import socket
import time

import paho.mqtt.client as mqtt

BROKER_HOST = os.environ.get("BOOTH_BROKER", "pb-node1.local")
BROKER_PORT = int(os.environ.get("BOOTH_BROKER_PORT", "1883"))


class T:
    """MQTT topic names (see docs/MQTT_PROTOCOL.md)."""

    ALL = "booth/#"
    STATUS = "booth/status/{node}"
    PROXIMITY = "booth/sensor/proximity"
    SESSION_STATE = "booth/session/state"
    GESTURE = "booth/vision/gesture"
    FACES = "booth/vision/faces"
    GAZE = "booth/vision/gaze"
    CAPTURE_CMD = "booth/cmd/capture"
    CAPTURE_DONE = "booth/capture/done"
    COMPOSE_REQUEST = "booth/compose/request"
    COMPOSE_RESULT = "booth/compose/result"


def now_ms() -> int:
    """Current Unix time in milliseconds (used for every `ts` field)."""
    return int(time.time() * 1000)


def make_payload(**fields) -> str:
    """Build a JSON payload and add `ts` if it is missing."""
    fields.setdefault("ts", now_ms())
    return json.dumps(fields, ensure_ascii=False)


def parse_payload(raw: bytes):
    """Decode a JSON payload. Returns None if it is not valid JSON."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def connect(node_name: str, host: str = None, port: int = None,
            on_message=None, subscriptions=()) -> mqtt.Client:
    """Create a client, register online/offline status and connect.

    - Publishes {"state": "online"} to booth/status/<node> (retained) on connect.
    - Registers a Last Will so the broker publishes "offline" if the node dies.
    - Re-subscribes to `subscriptions` after every reconnect.

    `subscriptions` is a list of (topic, qos) tuples.
    Call client.loop_start() or client.loop_forever() afterwards.
    """
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"{node_name}-{socket.gethostname()}")
    status_topic = T.STATUS.format(node=node_name)
    client.will_set(status_topic, make_payload(node=node_name, state="offline"),
                    qos=1, retain=True)

    def _on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[{node_name}] MQTT connect failed: {reason_code}")
            return
        c.publish(status_topic, make_payload(node=node_name, state="online"),
                  qos=1, retain=True)
        for topic, qos in subscriptions:
            c.subscribe(topic, qos)
        print(f"[{node_name}] connected to MQTT broker")

    def _on_disconnect(c, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"[{node_name}] MQTT disconnected ({reason_code}), reconnecting...")

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    if on_message is not None:
        client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect(host or BROKER_HOST, port or BROKER_PORT, keepalive=30)
    return client


def disconnect(client: mqtt.Client, node_name: str) -> None:
    """Publish a clean "offline" status, then disconnect."""
    info = client.publish(T.STATUS.format(node=node_name),
                          make_payload(node=node_name, state="offline"),
                          qos=1, retain=True)
    try:
        info.wait_for_publish(timeout=2)
    except (RuntimeError, ValueError):
        pass
    client.disconnect()
