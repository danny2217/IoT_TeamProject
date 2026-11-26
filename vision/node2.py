"""Node 2 — 정면 카메라: 제스처 · 인원수 발행, 촬영 명령 처리.

    python -m vision.node2 --dry-run --show         # 브로커 없이 화면으로 인식 확인
    python -m vision.node2 --dry-run --web 8080     # 모니터 없이: 브라우저에서 http://raspi.local:8080
    python -m vision.node2 --host 172.20.10.4       # 브로커에 발행
    python -m vision.node2 --node4 http://pb-node4.local:5000   # 촬영 사진을 Node 4로 전송

구독: booth/session/state, booth/cmd/capture
발행: booth/status/node2, booth/vision/gesture, booth/vision/faces, booth/vision/leader,
      booth/vision/gaze (카운트다운 중 모두 카메라를 보는지), booth/capture/done
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
from vision.gaze import GazeMonitor, gaze_features, is_looking, photo_gaze
from vision.gestures import ALLOWED, GestureStabilizer, classify
from vision.hands import create_hand_tracker
from vision.leader import LOCKED, LeaderTracker
from vision.occlusion import OccludedFaceMemory
from vision.smile import SmileScorer

NODE = "node2"
PROCESS_SIZE = (320, 240)                    # 인식은 저해상도, 촬영은 원본 해상도
FACE_STATES = {None, "IDLE", "COUNTDOWN"}    # 실시간 인원수를 세는 상태
FACE_INTERVAL_S = 0.3
LEADER_INTERVAL_S = 0.2                      # 대표자 손 위치 발행 간격(최대 초당 5번)
GAZE_STATES = {"COUNTDOWN"}                  # 시선을 보는 상태 (Node 1이 3·2·1 끝에 gaze_ok를 확인)
GAZE_PUB_S = 0.3                             # 시선 발행 간격 (바뀌면 바로, 그대로면 1초마다)


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


def _hand_tag(hand, chosen) -> str:
    """stat 줄용: 대표자면 *, 왼/오른손(L/R), 손 중심 x(0~320), 손 모양(펴진 손가락 수)."""
    mark = "*" if hand is chosen else ""
    side = (hand.side or "?")[0]
    cx = int(hand.box[0] + hand.box[2] / 2)
    return f"{mark}{side}x{cx}f{hand.shape.count}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Node 2 front camera vision")
    parser.add_argument("--host", default=None, help="MQTT 브로커 (기본 pb-node1.local)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--camera", default="0", help="USB 웹캠 번호 또는 pi(카메라 모듈)")
    parser.add_argument("--width", type=int, default=1280, help="촬영 해상도")
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--hands", default="auto", choices=["auto", "solutions", "tasks", "opencv"])
    parser.add_argument("--hold", type=float, default=0.5, help="제스처 유지 시간(초)")
    parser.add_argument("--smile", default="auto", choices=["auto", "mesh", "haar", "off"],
                        help="촬영 사진의 미소 지수 계산 방식 (off면 Node 4가 추정)")
    parser.add_argument("--max-hands", type=int, default=4, help="한 번에 찾을 손 개수 (1이면 대표자 기능 끔)")
    parser.add_argument("--hand-workers", type=int, default=3,
                        help="손 인식기 병렬 개수 (RPi 400 실측: 1개 5~7, 3개 12~15장/초)")
    parser.add_argument("--leader-lost", type=float, default=2.0, help="대표자 손이 이만큼 안 보이면 해제(초)")
    parser.add_argument("--out", default="data/captures", help="촬영 사진 저장 폴더")
    parser.add_argument("--node4", default=None, help="Node 4 주소, 예: http://pb-node4.local:5000")
    parser.add_argument("--show", action="store_true", help="미리보기 창 표시")
    parser.add_argument("--web", type=int, default=None, metavar="PORT",
                        help="모니터가 없을 때: 브라우저로 보는 미리보기 포트 (예: 8080)")
    parser.add_argument("--gaze", default="on", choices=["on", "off"],
                        help="시선 셔터: 카운트다운 중 모두 카메라를 보는지 booth/vision/gaze 로 발행")
    parser.add_argument("--dry-run", action="store_true", help="MQTT 없이 인식 결과만 출력")
    parser.add_argument("--stats", action="store_true",
                        help="1초마다 fps·손·얼굴(가려진 얼굴)·대표자 상태를 한 줄씩 출력 (실기 확인용)")
    args = parser.parse_args()

    camera = open_camera(args.camera, args.width, args.height)
    hands = create_hand_tracker(args.hands, max_hands=args.max_hands, workers=args.hand_workers)
    face_detector = FaceDetector()
    still_detector = FaceDetector()  # 촬영 스레드용
    smile = None if args.smile == "off" else SmileScorer(args.smile)
    gaze = None
    if args.gaze == "on":
        try:
            gaze = GazeMonitor()
        except Exception as exc:
            print(f"[{NODE}] 시선 판단 끔: {exc}")
    print(f"[{NODE}] 카메라 {camera.width}x{camera.height}, 얼굴 검출 {face_detector.name}, "
          f"미소 {smile.name if smile else 'off'}, 시선 {'on' if gaze else 'off'}")

    web = None
    if args.web:
        from vision.webview import WebPreview
        web = WebPreview(args.web, title="node2", scale=2)

    session = Session()
    out_dir = Path(args.out)
    client = None

    live = {"count": None, "time": 0.0}   # 손에 가려진 얼굴까지 센 실시간 인원수 (메인 루프가 갱신)

    def analyze(frame) -> dict:
        faces = still_detector.detect(frame, max_width=640)
        count = len(faces)
        # 촬영 순간 V 사인 등으로 얼굴이 가려지면 사진에서는 덜 잡힌다 → 직전 실시간 인원수로 보정
        if live["count"] is not None and time.monotonic() - live["time"] < 3.0:
            count = max(count, live["count"])
        info = {"face_count": count, "faces": faces}
        if smile is not None:
            total, per_face, marks = smile.analyze(frame, faces)
            for f, value in zip(faces, per_face):
                if value is not None:
                    f["smile"] = value
            if total is not None:
                info["smile_score"] = total
            looks = [is_looking(gaze_features(lm)) for lm in marks]
            for f, look in zip(faces, looks):
                if look is not None:
                    f["looking"] = look
            gaze_ok = photo_gaze(looks)
            if gaze_ok is not None:
                info["gaze_ok"] = gaze_ok
            print(f"[{NODE}] smile_score={total} 얼굴별 {per_face} 시선 {looks}")
        return info

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

    def publish(topic: str, quiet: bool = False, **fields) -> None:
        if not quiet:
            print(f"[{NODE}] {topic} {fields}")
        if client is not None:
            client.publish(topic, make_payload(**fields), qos=0)

    stabilizer = GestureStabilizer(hold_s=args.hold)
    leader = LeaderTracker(lost_s=args.leader_lost)
    occluded = OccludedFaceMemory()
    hidden_faces: list = []
    last_leader_status = None
    last_leader_pub = 0.0
    last_session_id = None
    seen_hands = []
    chosen = None
    face_count = CountDebouncer()
    last_faces: list[dict] = []
    last_seq = 0
    last_face_time = 0.0
    last_state = None
    gaze_ok, gaze_detail = None, []
    last_gaze_pub, last_gaze_sent = 0.0, "unset"
    fps, fps_time, fps_frames = 0.0, time.monotonic(), 0
    hand_fps, hand_frames = 0.0, 0
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
                if gaze is not None:
                    gaze.reset()
                gaze_ok, gaze_detail, last_gaze_sent = None, [], "unset"
                last_state = state
            # 세션이 끝나거나(ID가 바뀜) 절전이면 대표자 해제. 대기 중 첫 세션 ID가 생길 때는 유지.
            if (last_session_id is not None and session_id != last_session_id) or state == "SLEEP":
                leader.reset()
                occluded.reset()
            last_session_id = session_id
            small = cv2.resize(frame, PROCESS_SIZE)

            # 인원수: 상태가 맞을 때만, 0.3초에 한 번
            if state in FACE_STATES and now - last_face_time >= FACE_INTERVAL_S:
                last_face_time = now
                last_faces = face_detector.detect(small)
                if not ALLOWED.get(state):      # 카운트다운 중엔 제스처를 안 보므로 손만 따로 찾는다
                    result = hands.poll(small)
                    if result is not None:
                        seen_hands = result
                count, hidden_faces = occluded.update(last_faces, [h.box for h in seen_hands], now)
                live["count"], live["time"] = count, now
                changed = face_count.update(count)
                if changed is not None:
                    publish(T.FACES, session_id=session_id, face_count=changed,
                            hidden=len(hidden_faces))

            # 시선: 카운트다운 중에만. 계산은 GazeMonitor 스레드가 원본 해상도로 한다.
            if gaze is not None and state in GAZE_STATES:
                if last_faces:
                    fx, fy = frame.shape[1] / PROCESS_SIZE[0], frame.shape[0] / PROCESS_SIZE[1]
                    gaze.submit(frame, [{"x": int(f["x"] * fx), "y": int(f["y"] * fy),
                                         "w": int(f["w"] * fx), "h": int(f["h"] * fy)} for f in last_faces])
                gaze_ok, gaze_detail = gaze.result()
                if gaze_ok is not None and now - last_gaze_pub >= GAZE_PUB_S and (
                        gaze_ok != last_gaze_sent or now - last_gaze_pub >= 1.0):
                    looking = sum(1 for _, lk, _ in gaze_detail if lk)
                    publish(T.GAZE, quiet=gaze_ok == last_gaze_sent, session_id=session_id,
                            gaze_ok=gaze_ok, looking=looking, faces=len(gaze_detail))
                    last_gaze_pub, last_gaze_sent = now, gaze_ok

            # 제스처: 이 상태에서 받을 제스처가 있을 때만 손 모델을 돌린다
            confirmed = None
            result = None
            if ALLOWED.get(state):
                exclude = [(f["x"], f["y"], f["w"], f["h"]) for f in last_faces]
                result = hands.poll(small, exclude=exclude)   # 병렬이면 새 결과가 없을 때 None
            if result is not None:
                hand_frames += 1
                seen_hands = result
                before = leader.leader
                chosen = leader.pick(seen_hands, now)
                if leader.leader is not before and before is not None and leader.leader is None:
                    stabilizer.reset()          # 대표자 해제: 다음 사람은 처음부터
                gesture = classify(chosen.shape, state) if chosen else None
                confirmed = stabilizer.update(gesture, now)
                if confirmed:
                    if leader.leader is None and chosen is not None:
                        leader.lock(chosen, now)
                    name, value = confirmed
                    label = name if value is None else f"{name} {value}"
                    publish(T.GESTURE, session_id=session_id, gesture=name, value=value,
                            confidence=round(chosen.score, 2) if chosen else None)

                # 대표자 상태는 바뀔 때 바로, 위치는 고정 중일 때만 0.2초 간격으로 발행
                status = leader.status
                moved = status == LOCKED and now - last_leader_pub >= LEADER_INTERVAL_S
                if status != last_leader_status or moved:
                    box = None
                    if leader.leader is not None:
                        x, y, w, h = leader.leader.box
                        pw, ph = PROCESS_SIZE
                        box = {"x": round(x / pw, 3), "y": round(y / ph, 3),
                               "w": round(w / pw, 3), "h": round(h / ph, 3)}
                    publish(T.LEADER, quiet=status == last_leader_status,
                            session_id=session_id, status=status, box=box,
                            hands=len(seen_hands))
                    last_leader_status, last_leader_pub = status, now
            elif not ALLOWED.get(state):
                stabilizer.update(None, now)
                chosen = None

            fps_frames += 1
            if now - fps_time >= 1.0:
                fps = fps_frames / (now - fps_time)
                hand_fps = hand_frames / (now - fps_time)
                fps_time, fps_frames, hand_frames = now, 0, 0
                if args.stats:
                    gray = sum(1 for h in seen_hands if h is not chosen)
                    print(f"[stat] {time.strftime('%H:%M:%S')} fps={fps:.1f} hand_fps={hand_fps:.1f} "
                          f"hands={len(seen_hands)} "
                          f"(leader={1 if chosen is not None and leader.leader is not None else 0}, other={gray}) "
                          f"faces={len(last_faces)}+{len(hidden_faces)}hidden leader={leader.status} "
                          f"gesture={label or '-'} gaze={gaze_ok} "
                          f"[{' '.join(_hand_tag(h, chosen) for h in seen_hands)}]", flush=True)

            if args.show or web:
                view = small.copy()
                for f in last_faces:
                    cv2.rectangle(view, (f["x"], f["y"]), (f["x"] + f["w"], f["y"] + f["h"]),
                                  (0, 255, 255), 1)
                for (x, y, w, h) in hidden_faces:  # 손에 가려진 얼굴: 주황
                    cv2.rectangle(view, (int(x), int(y)), (int(x + w), int(y + h)), (0, 140, 255), 1)
                    cv2.putText(view, "hidden", (int(x), max(10, int(y) - 3)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 140, 255), 1)
                for hnd in seen_hands:          # 다른 사람 손: 회색 얇게, 대표자: 초록 굵게
                    x, y, w, h = hnd.box
                    if hnd is chosen and leader.leader is not None:
                        cv2.rectangle(view, (x, y), (x + w, y + h), (0, 220, 0), 2)
                        cv2.putText(view, "LEADER", (x, max(10, y - 4)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 220, 0), 1)
                    elif hnd is chosen:
                        cv2.rectangle(view, (x, y), (x + w, y + h), (0, 200, 255), 1)
                    else:
                        cv2.rectangle(view, (x, y), (x + w, y + h), (150, 150, 150), 1)
                if state in GAZE_STATES and gaze_detail:   # 시선: 보면 초록, 아니면 빨강 + 이유
                    fx, fy = PROCESS_SIZE[0] / frame.shape[1], PROCESS_SIZE[1] / frame.shape[0]
                    for f, look, why in gaze_detail:
                        color = (0, 220, 0) if look else (0, 0, 255) if look is False else (160, 160, 160)
                        x, y = int(f["x"] * fx), int((f["y"] + f["h"]) * fy)
                        cv2.putText(view, why, (x, min(PROCESS_SIZE[1] - 4, y + 12)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1)
                    if gaze_ok is False:
                        cv2.putText(view, "look at the camera", (6, 52),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                if leader.status == "missing":
                    cv2.putText(view, "show leader hand again", (6, 34),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
                cv2.putText(view, f"{state or '-'} {fps:.0f}/{hand_fps:.0f}fps faces:{len(last_faces)}+{len(hidden_faces)} "
                            f"hands:{len(seen_hands)}",
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
        if gaze is not None:
            gaze.close()
        if smile is not None:
            smile.close()
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
