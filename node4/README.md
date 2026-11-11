# Node 4 — 네컷 합성 · 저장 · QR

유현종 담당(RPi 4) 코드다. 라즈베리파이와 카메라 없이 PC에서 제안서 6.5와 MQTT 규약 v1을 검증한다.

## 오늘 PC에서 확인한 것

- `face_count` 2 이하면 세로 4컷, 3 이상이면 2×2 와이드
- 프레임 색 `white` `pink` `blue` `black`
- `smile_score`가 가장 높은 컷을 첫 칸에 두고 Best Smile 뱃지
- 얼굴 좌표가 칸 중앙에 오도록 크롭. 좌표가 없으면 OpenCV Haar, 그것도 없으면 가운데 크롭
- 사진이 하나라도 없으면 합성하지 않고 `missing shot 2 side` 형식의 오류
- 세션 DB(SQLite), 결과 JPEG, 다운로드 페이지, QR
- `AWS_S3_BUCKET`이 있으면 S3에 올리고, 없거나 실패하면 로컬 파일을 쓴다

브로커는 규약대로 Node 1이다. AWS는 결과 저장과 다운로드용이다.

## 실행

프로젝트 폴더에서:

```powershell
pip install -r requirements.txt
python -m unittest discover -s tests
python -m node4 demo
python -m node4 serve
```

`demo` 결과는 `samples/output/`에 생긴다.

- `vertical_face2_pink.jpg` — 2명, 세로, 분홍 프레임
- `wide_face3_blue.jpg` — 3명, 2×2, 파란 프레임
- `slide12_samples.png` — 발표 12번 슬라이드에 넣을 한 장

서버를 띄운 뒤 합성만 따로 호출할 때:

```powershell
python -m node4 serve --mqtt
```

`--mqtt`는 `BOOTH_BROKER`(기본 `pb-node1.local`)에 연결한다. 브로커가 없으면 HTTP만 계속된다.

## 성환님 사진 업로드

`POST /api/photos` multipart.

| 필드 | 값 |
| --- | --- |
| file | JPEG |
| session_id | `S20261111-142501` |
| shot | `1` 또는 `2` |
| angle | `front`(Node 2) 또는 `side`(Node 3) |
| node | `node2` / `node3` |
| face_count | 정면만 |
| smile_score | 0~100, 없으면 Node 4가 추정 |
| gaze_ok | true/false |
| faces | `[{"x":412,"y":180,"w":160,"h":160}]` |
| filename | 생략하면 `{session}_{shot}_{angle}.jpg` |

파일 전송이 끝난 뒤 기존처럼 `booth/capture/done`을 보낸다. Node 1이 `booth/compose/request`로 `frame_color`를 보내면 Node 4가 `booth/compose/result`로 다운로드 URL을 돌려준다.

로컬에서 MQTT 없이 합성을 시키려면 `POST /api/compose`

```json
{"session_id": "S20261111-142501", "frame_color": "pink"}
```

결과 페이지는 `GET /download/{session_id}`, 이미지는 `GET /results/{session_id}.jpg`, QR은 `GET /qr/{session_id}.png`.

## AWS

버킷을 쓸 때만:

```powershell
$env:AWS_S3_BUCKET = "버킷이름"
$env:AWS_REGION = "ap-northeast-2"
pip install boto3
```

스키마는 `node4/schema.sql`. 로컬은 SQLite(`data/booth.db` 또는 demo의 `booth_data`)이고, RDS로 옮길 때 같은 컬럼을 쓰면 된다.

얼굴 좌표가 없고 OpenCV 5라 Haar Cascade가 없으면 `node4/models/face_detection_yunet_2023mar.onnx`를 쓴다. 모델은 [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)에서 받았다.

## 실기에서 이어서 할 일

1. Node 4(RPi 3B)에서 `python -m node4 serve --host 0.0.0.0 --mqtt`를 실행한다. `common/booth_mqtt.py`는 막심님 브랜치의 파일을 그대로 둔다.
2. 성환님 HTTP 주소와 위 필드를 맞춘 뒤, 실제 사진 4장으로 한 세션을 돌린다.
3. AWS 계정이 정해지면 `AWS_S3_BUCKET`과 `BOOTH_PUBLIC_BASE`를 넣고 합성 결과 한 장을 올린다. QR이 그 주소를 담는다.
