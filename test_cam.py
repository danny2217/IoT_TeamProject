# 1_test_cam.py
import cv2

# USB 웹캠 연결 (RPi 2는 정면, RPi 3는 측면/상단 구도)
cap = cv2.VideoCapture(0)

# RPi 3B 연산 최적화를 위해 320x240으로 해상도 고정
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

if not cap.isOpened():
    print("카메라는 열 수 없습니다. USB 연결 및 /dev/video0을 확인하세요.")
    exit()

print("카메라 연결 성공! 'q'를 누르면 종료합니다.")

while True:
    ret, frame = cap.read()
    if not ret:
        print(" 프레임을 읽어올 수 없습니다.")
        break

    cv2.imshow("Webcam Test (320x240)", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()