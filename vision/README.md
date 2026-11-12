# vision — Node 2 · Node 3

장성환 담당 코드다. 규약은 `docs/MQTT_PROTOCOL.md`(v1), 통신은 `common/booth_mqtt.py`를 그대로 쓴다.

| 노드 | 장비 | 실행 | 하는 일 |
| --- | --- | --- | --- |
| Node 2 | RPi 400 + 정면 웹캠 | `python -m vision.node2` | 제스처 4종, 인원수, 예약 시각 촬영 |
| Node 3 | RPi 3B + 5" LCD(800×480 가로) + 보조 웹캠 | `python -m vision.node3 --show` | 예약 시각 촬영, LCD 상태 표시 |

## 설치

Node 3 (시스템 Python에 OpenCV·paho가 있으면 추가 설치 불필요):

```bash
pip install "paho-mqtt>=2.0" requests opencv-python
```

Node 2 (RPi 400). MediaPipe 1.x는 AES 명령어가 없는 RPi 4/400 CPU에서 `Illegal instruction`으로 죽고,
Python 3.13용 0.10.x 휠은 없다. 그래서 Python 3.12 가상환경에 0.10.18을 쓴다.

```bash
pip install uv            # 또는 python3 -m venv .venv && .venv/bin/pip install uv
uv venv --python 3.12 .venv312
uv pip install --python .venv312/bin/python "mediapipe==0.10.18" "paho-mqtt>=2.0" requests
.venv312/bin/python -m vision.node2 --dry-run --show
```

## 실행

프로젝트 폴더에서 실행한다.

```bash
python -m vision.node2 --dry-run --show            # 브로커 없이 인식만 확인 (q 종료)
python -m vision.node2 --dry-run --web 8080        # 모니터 없는 Node 2: 다른 PC 브라우저에서 http://raspi.local:8080
python -m vision.node2 --host <브로커 IP> --show
python -m vision.node3 --host <브로커 IP> --show
python -m vision.node3 --host <브로커 IP> --show --camera pi   # 라즈베리 파이 카메라 모듈
```

`--node4 http://<Node 4 IP>:<포트>`를 주면 촬영한 사진을 `POST /api/photos`로 보낸 뒤 `booth/capture/done`을 발행한다. 주지 않으면 `data/captures/`에 저장하고 발행만 한다.

브로커에서 확인:

```bash
mosquitto_sub -h <브로커 IP> -t 'booth/#' -v
```

가짜 세션 상태와 촬영 명령 보내기(`capture_at`은 현재 시각 + 3초):

```bash
mosquitto_pub -h <브로커 IP> -t booth/session/state -q 1 -m '{"session_id":"S20261111-150000","state":"SELECT","ts":0}'
mosquitto_pub -h <브로커 IP> -t booth/cmd/capture -q 1 -m "{\"session_id\":\"S20261111-150000\",\"shot\":1,\"capture_at\":$(( $(date +%s%3N) + 3000 )),\"ts\":0}"
```

## 세션 상태별 동작 (Node 2)

| 상태 | 받는 제스처 | 인원수 |
| --- | --- | --- |
| 상태 모름(단독 테스트) | 전부 | 발행 |
| `SLEEP` | `PALM` | — |
| `IDLE` | `PALM`, `V_SIGN` | 발행 |
| `COUNTDOWN` | — | 발행 |
| `SELECT` | `FINGERS` 1~4, `THUMBS_UP` | — |
| 그 외 | — | — |

V 사인과 손가락 2개, 손바닥과 손가락 4개는 모양이 같아서 상태로 구분한다. 표는 `vision/gestures.py`의 `ALLOWED`에서 바꾼다.

## 손 인식 방식

`--hands auto`는 아래 순서로 되는 것을 쓴다.

1. `solutions` — MediaPipe 0.10.x API. Node 2의 `.venv312`가 이 방식이다(빈 화면 기준 프레임당 약 100ms).
2. `tasks` — MediaPipe Tasks API. `vision/models/hand_landmarker.task` 파일이 있어야 한다.
3. `opencv` — 피부색 윤곽 + convexity defects. 조명·배경에 민감하고 엄지 올리기는 구분하지 못한다(버튼으로 대신).

## 테스트

```bash
python -m unittest tests.test_vision_gestures
```

제스처 분류와 유지 시간 로직만 검증한다. 카메라·MediaPipe·MQTT 연동은 실기에서 확인해야 한다.

## Node 3의 HC-SR04

`node3_sensor/proximity.py`(막심)와 `vision.node3`을 같이 실행하면 둘 다 `node3`로 접속해 브로커가 한쪽을 끊는다. `python -m vision.node3 --with-proximity`로 한 프로세스에서 같이 돌리거나, 막심 님과 클라이언트 이름을 나누기로 정한다.
