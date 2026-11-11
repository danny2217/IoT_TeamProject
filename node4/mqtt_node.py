"""MQTT 규약 v1의 Node 4 구독.

구독: booth/capture/done, booth/compose/request
발행: booth/compose/result, booth/status/node4
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from node4.service import Node4Service

CAPTURE_DONE = "booth/capture/done"
COMPOSE_REQUEST = "booth/compose/request"
COMPOSE_RESULT = "booth/compose/result"


def handle_message(service: Node4Service, topic: str, payload: dict) -> dict | None:
    """브로커 없이 테스트할 수 있게 메시지 처리와 연결을 나눴다."""
    if not isinstance(payload, dict):
        return None
    if topic == CAPTURE_DONE:
        service.note_capture(payload)
        return None
    if topic == COMPOSE_REQUEST:
        session_id = payload.get("session_id")
        if not session_id:
            return None
        try:
            return service.compose(str(session_id), str(payload.get("frame_color") or "white"))
        except ValueError as exc:
            return {
                "session_id": session_id,
                "status": "ERROR",
                "layout": None,
                "best_shot": None,
                "url": None,
                "error": str(exc),
                "ts": 0,
            }
    return None


def start_mqtt(service: Node4Service, host: str | None = None, port: int | None = None):
    """브로커에 붙는다. 실패하면 예외를 올린다. HTTP 서버는 호출하는 쪽에서 계속 띄운다."""
    from common.booth_mqtt import T, connect, parse_payload

    def on_message(client, userdata, msg):
        data = parse_payload(msg.payload)
        if not isinstance(data, dict):
            print("[node4] JSON이 아닌 MQTT 메시지는 무시합니다.")
            return
        result = handle_message(service, msg.topic, data)
        if result is None:
            return
        client.publish(T.COMPOSE_RESULT, json.dumps(result, ensure_ascii=False), qos=1)
        if result.get("status") == "OK":
            print(f"[node4] 합성 완료 {result.get('session_id')} {result.get('url')}")
        else:
            print(f"[node4] 합성 실패 {result.get('session_id')} {result.get('error')}")

    client = connect(
        "node4",
        host=host,
        port=port,
        on_message=on_message,
        subscriptions=[(T.CAPTURE_DONE, 1), (T.COMPOSE_REQUEST, 1)],
    )
    client.loop_start()
    return client
