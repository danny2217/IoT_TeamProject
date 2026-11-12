# 2_test_mediapipe_fps.py
import cv2
import mediapipe as mp
import time

mp_hands = mp.solutions.hands
mp_face_detection = mp.solutions.face_detection
mp_drawing = mp.solutions.drawing_utils

# 3B 연산 최적화 설정
hands = mp_hands.Hands(
    max_num_hands=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
face_detection = mp_face_detection.FaceDetection(min_detection_confidence=0.5)

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

prev_time = 0
print(" MediaPipe FPS 측정 시작 ('q' 누르면 종료)")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # 1. 손 관절 추적 (제스처용)
    hand_results = hands.process(rgb_frame)
    if hand_results.multi_hand_landmarks:
        for hand_landmarks in hand_results.multi_hand_landmarks:
            mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

    # 2. 얼굴 감지 (인원수 face_count 산출용)
    face_results = face_detection.process(rgb_frame)
    if face_results.detections:
        face_count = len(face_results.detections)
        cv2.putText(frame, f"Faces: {face_count}", (10, 60), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    # Real-time FPS 계산
    curr_time = time.time()
    fps = 1 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 0
    prev_time = curr_time

    cv2.putText(frame, f"FPS: {int(fps)}", (10, 30), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    cv2.imshow("MediaPipe Performance Test", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

hands.close()
face_detection.close()
cap.release()
cv2.destroyAllWindows()