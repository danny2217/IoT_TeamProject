"""동시 촬영 시차 측정 (교수님 피드백 ①: 실제 찍힌 것 기준).

Node 2·3을 평소처럼 켜 둔 상태에서, 두 카메라가 같은 ms 타이머 화면(중간데모_준비/ms_timer.html)을
보게 놓고 실행한다. 세션과 상관없는 SYNC- 세션 ID로 촬영 명령만 보낸다.

    python -m vision.synctest --host 172.20.10.4 --count 20

출력
- 노드별 "프레임 받은 시각 - 예약 시각" (각자 시계 기준)
- 두 노드 프레임 시각 차 (시계가 맞다는 전제의 소프트웨어 시차)
- 사진은 각 노드의 data/captures/SYNC-..._<n>_front|side.jpg
  → 두 사진에 찍힌 타이머 숫자 차이가 실제 시차 (사진을 보고 표에 적는다)
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time

from common.booth_mqtt import T, connect, disconnect, make_payload, now_ms


def main() -> None:
    parser = argparse.ArgumentParser(description="동시 촬영 시차 측정")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--lead", type=int, default=1000, help="명령 후 촬영까지 ms")
    parser.add_argument("--gap", type=float, default=2.5, help="촬영 간격(초)")
    args = parser.parse_args()

    base = time.strftime("SYNC-%H%M%S")
    # Node 4는 한 세션에 1·2번 컷만 받으므로 2컷마다 세션 이름을 바꾼다.
    def ident(k: int) -> tuple[str, int]:
        return f"{base}-{(k - 1) // 2 + 1}", (k - 1) % 2 + 1

    index = {ident(k): k for k in range(1, args.count + 1)}
    done: dict[int, dict[str, dict]] = {}
    lock = threading.Lock()

    def on_message(client, userdata, msg):
        data = json.loads(msg.payload)
        k = index.get((data.get("session_id"), int(data.get("shot") or 0)))
        if k is None:
            return
        with lock:
            done.setdefault(k, {})[data["node"]] = data

    client = connect("synctest", args.host, args.port, on_message=on_message,
                     subscriptions=[(T.CAPTURE_DONE, 1)])
    client.loop_start()
    time.sleep(1.0)
    for k in range(1, args.count + 1):
        session, shot = ident(k)
        capture_at = now_ms() + args.lead
        client.publish(T.CAPTURE_CMD, make_payload(session_id=session, shot=shot,
                                                    capture_at=capture_at), qos=1)
        time.sleep(args.gap)
    time.sleep(2.0)
    client.loop_stop()
    disconnect(client, "synctest")

    print(f"세션 {base}-1 ~ {base}-{(args.count + 1) // 2} (2컷씩)")
    print(f"{'컷':>3} {'node2 지연':>10} {'node3 지연':>10} {'두 프레임 차':>12}")
    gaps = []
    for shot in range(1, args.count + 1):
        row = done.get(shot, {})
        n2, n3 = row.get("node2"), row.get("node3")
        l2 = n2.get("late_ms") if n2 else None
        l3 = n3.get("late_ms") if n3 else None
        gap = (n2["frame_ts"] - n3["frame_ts"]) if n2 and n3 and "frame_ts" in n2 and "frame_ts" in n3 else None
        if gap is not None:
            gaps.append(gap)
        fmt = lambda v: "-" if v is None else f"{v:+d}ms"  # noqa: E731
        print(f"{shot:>3} {fmt(l2):>10} {fmt(l3):>10} {fmt(gap):>12}")
    if gaps:
        absg = [abs(g) for g in gaps]
        print(f"두 프레임 차: 평균 {statistics.mean(absg):.1f}ms, 최대 {max(absg)}ms (n={len(gaps)})")
    print("실제 시차는 두 사진 속 타이머 숫자 차이로 확인하세요.")


if __name__ == "__main__":
    main()
