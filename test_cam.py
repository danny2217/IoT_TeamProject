# test_cam.py
import cv2

# 0번 카메라 연결 (USB 웹캠)
cap = cv2.VideoCapture(0)

# RPi 3B 연산 최적화를 위한 320x240 해상도 설정
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

if not cap.isOpened():
    print("❌ 웹캠을 열 수 없습니다. USB 연결 및 /dev/video0 을 확인하세요.")
    exit()

print("✅ 웹캠 연결 성공! 'q'를 누르면 종료합니다.")

while True:
    ret, frame = cap.read()
    if not ret:
        print("❌ 프레임을 읽어올 수 없습니다.")
        break

    # 화면 표시
    cv2.imshow("Webcam Test (320x240)", frame)

    # 'q' 키 누르면 종료
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()