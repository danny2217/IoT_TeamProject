"""노드 간 시계 차이 측정 (교수님 피드백 ②: NTP 검증).

동시 촬영은 각 노드가 자기 시계로 capture_at을 기다리므로, 노드 시계가 서로 얼마나 맞는지가
곧 촬영 시차의 한 부분이다. chrony/timesyncd 가 "맞췄다"고 하는 값과 별개로 MQTT로 직접 잰다.

    # 재고 싶은 노드마다 (Node 2, 3, 4 ...)
    python -m vision.clockcheck reply --node node3 --host 172.20.10.4
    # 기준 노드(Node 1 또는 브로커가 있는 노드)에서
    python -m vision.clockcheck ping --host 172.20.10.4 --count 20

원리 (NTP와 같은 계산): 기준이 t1에 보냄 → 노드가 자기 시계 t2로 답함 → 기준이 t3에 받음
    시계 차 ≈ t2 - (t1 + t3) / 2      (+면 그 노드 시계가 기준보다 빠름)
    오차 범위 = ± (t3 - t1) / 2       (왕복 시간이 짧은 측정일수록 정확)
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time

from common.booth_mqtt import connect, disconnect, now_ms

PING = "booth/debug/clock/ping"
PONG = "booth/debug/clock/pong"


def offset_of(t1: int, t2: int, t3: int) -> tuple[float, float]:
    """(시계 차 ms, 왕복 ms)."""
    return t2 - (t1 + t3) / 2, float(t3 - t1)


def summarize(samples: list[tuple[float, float]], keep: float = 0.5) -> dict:
    """왕복이 짧은 절반만 써서 시계 차를 낸다 (네트워크가 잠깐 느린 측정은 버림)."""
    if not samples:
        return {}
    best = sorted(samples, key=lambda s: s[1])[:max(1, int(len(samples) * keep))]
    offsets = [o for o, _ in best]
    return {"n": len(samples), "used": len(best),
            "offset_ms": round(statistics.median(offsets), 1),
            "offset_min": round(min(offsets), 1), "offset_max": round(max(offsets), 1),
            "rtt_median_ms": round(statistics.median(r for _, r in samples), 1),
            "error_ms": round(min(r for _, r in best) / 2, 1)}


def run_reply(args) -> None:
    def on_message(client, userdata, msg):
        t2 = now_ms()
        data = json.loads(msg.payload)
        client.publish(PONG, json.dumps({"id": data["id"], "node": args.node, "t1": data["t1"], "t2": t2}))

    client = connect(f"clock-{args.node}", args.host, args.port, on_message=on_message,
                     subscriptions=[(PING, 0)])
    print(f"[clock] {args.node} 응답 대기 (Ctrl+C 종료)")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        disconnect(client, f"clock-{args.node}")


def run_ping(args) -> None:
    results: dict[str, list[tuple[float, float]]] = {}
    lock = threading.Lock()

    def on_message(client, userdata, msg):
        t3 = now_ms()
        data = json.loads(msg.payload)
        with lock:
            results.setdefault(data["node"], []).append(offset_of(data["t1"], data["t2"], t3))

    client = connect("clock-ping", args.host, args.port, on_message=on_message,
                     subscriptions=[(PONG, 0)])
    client.loop_start()
    time.sleep(1.0)
    for i in range(args.count):
        client.publish(PING, json.dumps({"id": i, "t1": now_ms()}))
        time.sleep(args.interval)
    time.sleep(1.0)
    client.loop_stop()
    disconnect(client, "clock-ping")

    if not results:
        print("[clock] 응답한 노드가 없습니다. 각 노드에서 reply 를 먼저 켜세요.")
        return
    print(f"{'노드':8} {'시계 차(중앙값)':>14} {'범위':>16} {'왕복':>8} {'측정 오차':>10}")
    for node, samples in sorted(results.items()):
        s = summarize(samples)
        print(f"{node:8} {s['offset_ms']:>+12.1f}ms {s['offset_min']:>+7.1f}~{s['offset_max']:<+7.1f}"
              f" {s['rtt_median_ms']:>6.1f}ms {'±'}{s['error_ms']:.1f}ms  (n={s['n']})")
    print("시계 차 +: 그 노드 시계가 이 컴퓨터보다 빠름. 두 노드끼리의 차 = 두 값의 차이.")


def main() -> None:
    parser = argparse.ArgumentParser(description="노드 간 시계 차이 측정")
    parser.add_argument("role", choices=["ping", "reply"])
    parser.add_argument("--node", default="node", help="reply 쪽 이름 (node2, node3 ...)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    run_reply(args) if args.role == "reply" else run_ping(args)


if __name__ == "__main__":
    main()
