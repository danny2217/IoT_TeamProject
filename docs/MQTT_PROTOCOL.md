# MQTT 통신 규약 (v1)

모든 노드는 이 문서의 토픽 이름과 Payload 형식을 따른다. 토픽 이름은 코드에 직접 쓰지 말고 `common/booth_mqtt.py`의 `T` 상수를 사용한다.

## 1. 기본 규칙

| 항목 | 규칙 |
| --- | --- |
| 브로커 | Node 1 (RPi 400), 호스트 이름 `pb-node1.local`, 포트 `1883` |
| 노드 이름 | `node1` (RPi 400), `node2` (정면 RPi 3B), `node3` (보조 RPi 3B LCD), `node4` (합성 RPi 3B) |
| 호스트 이름 | `pb-node1` ~ `pb-node4` (Raspberry Pi Imager에서 설정) |
| Payload | UTF-8 JSON 객체 |
| 시간 `ts` | 모든 Payload에 포함, Unix epoch 밀리초(ms). 노드 시간은 NTP로 동기화 |
| 세션 ID | `S` + `YYYYMMDD-HHMMSS` (예: `S20261123-142501`), Node 1이 발급 |
| 촬영 회차 `shot` | 1, 2 (회차마다 정면 1장 + 보조 1장, 총 4장) |
| QoS | 명령·결과는 QoS 1, 실시간 비전 데이터는 QoS 0 |
| Retain | 노드 상태와 세션 상태만 retain |

## 2. 토픽 목록

| 토픽 | 발행 → 구독 | QoS / Retain | 설명 |
| --- | --- | --- | --- |
| `booth/status/{node}` | 각 노드 → Node 1 | 1 / retain | online·offline (offline은 LWT로 자동 발행) |
| `booth/sensor/proximity` | Node 3 → Node 1 | 1 | HC-SR04 근접 이벤트 (WAKE_UP, LEAVE) |
| `booth/session/state` | Node 1 → 전체 | 1 / retain | 현재 세션 상태 |
| `booth/vision/gesture` | Node 2 → Node 1 | 0 | 제스처 인식 결과 |
| `booth/vision/faces` | Node 2 → Node 1 | 0 | 실시간 인원수(face_count), 레이아웃 미리보기용 |
| `booth/vision/gaze` | Node 2 → Node 1 | 0 | 시선 정면 여부 |
| `booth/cmd/capture` | Node 1 → Node 2, 3 | 1 | 촬영 명령 (예약 시각 포함) |
| `booth/capture/done` | Node 2, 3 → Node 1, 4 | 1 | 촬영 완료 + 분석 결과 |
| `booth/compose/request` | Node 1 → Node 4 | 1 | 합성 요청 (프레임 색상 확정 후) |
| `booth/compose/result` | Node 4 → Node 1 | 1 | 합성·업로드 결과와 다운로드 URL |

## 3. Payload 형식

### 3.1 `booth/status/{node}`

```json
{"node": "node3", "state": "online", "ts": 1763870701000}
```

`state`: `online` | `offline`

### 3.2 `booth/sensor/proximity`

```json
{"event": "WAKE_UP", "distance_cm": 82.4, "ts": 1763870701000}
```

- `event`: `WAKE_UP` (1m 이내 진입이 연속 감지됨) | `LEAVE` (1.3m 밖으로 일정 시간 벗어남)
- 사람이 있는 동안 WAKE_UP은 한 번만 보낸다. LEAVE 후 3초 동안은 다시 보내지 않는다 (중복 방지).

### 3.3 `booth/session/state`

```json
{"session_id": "S20261123-142501", "state": "COUNTDOWN", "shot": 1, "layout": "vertical", "ts": 1763870701000}
```

`state` 값과 흐름:

```
SLEEP → IDLE → COUNTDOWN → CAPTURE → (shot 2: COUNTDOWN → CAPTURE) → SELECT → COMPOSE → DONE → IDLE
                                                         어느 단계든 실패 시 → ERROR
```

| state | 의미 |
| --- | --- |
| `SLEEP` | 절전 화면, 사람 없음 |
| `IDLE` | WAKE_UP 이후 시작 대기 (손바닥 제스처 / 버튼) |
| `COUNTDOWN` | V 사인 인식 후 카운트다운, 시선 확인 중 |
| `CAPTURE` | 촬영 명령 발행, 사진 수신 대기 |
| `SELECT` | 프레임 색상 선택 (손가락 개수 + 엄지 확정) |
| `COMPOSE` | Node 4 합성·업로드 중 |
| `DONE` | QR 표시 |
| `ERROR` | 오류 안내, 재시도 대기 |

`layout`: `vertical` (face_count ≤ 2) | `wide` (face_count ≥ 3), 아직 모르면 생략

### 3.4 `booth/vision/gesture`

```json
{"session_id": "S20261123-142501", "gesture": "FINGERS", "value": 3, "confidence": 0.91, "ts": 1763870701000}
```

- `gesture`: `PALM` (시작) | `V_SIGN` (셔터) | `FINGERS` (프레임 색상, `value` = 1~4) | `THUMBS_UP` (확정)
- 같은 제스처가 연속 프레임에서 유지되었을 때만 1회 발행한다.
- 세션이 없을 때(IDLE 이전)는 `session_id`를 `null`로 보낸다.

### 3.5 `booth/vision/faces`

```json
{"session_id": "S20261123-142501", "face_count": 2, "ts": 1763870701000}
```

값이 바뀔 때만 발행한다.

### 3.6 `booth/vision/gaze`

```json
{"session_id": "S20261123-142501", "gaze_ok": false, "ts": 1763870701000}
```

COUNTDOWN 상태에서만 발행한다. 여러 명이면 모든 얼굴이 정면일 때 `gaze_ok: true`.

### 3.7 `booth/cmd/capture`

```json
{"session_id": "S20261123-142501", "shot": 1, "capture_at": 1763870703500, "ts": 1763870701000}
```

`capture_at`: 두 카메라가 동시에 셔터를 누를 예약 시각 (epoch ms). 명령 수신 후 이 시각까지 대기했다가 촬영한다.

### 3.8 `booth/capture/done`

```json
{
  "session_id": "S20261123-142501",
  "shot": 1,
  "node": "node2",
  "angle": "front",
  "file": "S20261123-142501_1_front.jpg",
  "face_count": 2,
  "smile_score": 87,
  "gaze_ok": true,
  "faces": [{"x": 412, "y": 180, "w": 160, "h": 160}],
  "ts": 1763870703620
}
```

- `angle`: `front` (Node 2) | `side` (Node 3)
- `faces`: 원본 해상도 기준 얼굴 bounding box (픽셀). 없으면 `[]`
- Node 3는 `smile_score`, `gaze_ok`를 보내지 않는다 (Node 4가 계산).
- 사진 파일 자체는 HTTP로 Node 4에 보낸다. 이 메시지는 전송이 끝난 뒤 발행한다.

### 3.9 `booth/compose/request`

```json
{"session_id": "S20261123-142501", "frame_color": "pink", "ts": 1763870710000}
```

`frame_color`: `white` (1) | `pink` (2) | `blue` (3) | `black` (4) — 손가락 개수와 대응

### 3.10 `booth/compose/result`

```json
{"session_id": "S20261123-142501", "status": "OK", "layout": "vertical", "best_shot": "S20261123-142501_2_front.jpg",
 "url": "https://…/S20261123-142501", "error": null, "ts": 1763870715000}
```

`status`: `OK` | `ERROR` (`error`에 사유, 예: `"missing shot 2 side"`)

## 4. 테스트 명령

```bash
# 모든 메시지 보기 (Node 1 또는 같은 Wi-Fi의 아무 기기)
mosquitto_sub -h pb-node1.local -t 'booth/#' -v

# 가짜 WAKE_UP 보내기
mosquitto_pub -h pb-node1.local -t booth/sensor/proximity -q 1 \
  -m '{"event":"WAKE_UP","distance_cm":80,"ts":0}'
```

## 5. 변경 규칙

규약을 바꿀 때는 이 문서를 먼저 수정하고 PR로 팀원 확인을 받은 뒤 코드에 반영한다.
