# test_mediapipe.py
import cv2
import mediapipe as mp
import time

# MediaPipe Hands 모듈 초기화
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils

# RPi 3B용 경량화 설정 (Detect/Track 임계값 0.5)
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,            # 1개의 손만 추적하여 연산 감소
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

prev_time = 0

print("✅ MediaPipe 손 관절 추적 테스트 시작! 'q'를 누르면 종료합니다.")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    # BGR 이미지를 RGB로 변환 (MediaPipe 입력용)
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = hands.process(rgb_frame)

    # 손이 감지되면 21개 관절 그리기
    if results.multi_hand_landmarks:
        for hand_landmarks in results.multi_hand_landmarks:
            mp_drawing.draw_landmarks(
                frame, hand_landmarks, mp_hands.HAND_CONNECTIONS
            )

    # FPS(초당 프레임 수) 계산 및 표시
    curr_time = time.time()
    fps = 1 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 0
    prev_time = curr_time

    cv2.putText(frame, f"FPS: {int(fps)}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    cv2.imshow("MediaPipe Hands Test", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

hands.close()
cap.release()
cv2.destroyAllWindows()