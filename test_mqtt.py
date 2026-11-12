# 3_test_mqtt.py
import paho.mqtt.client as mqtt
import time

# [설정] RPi 1의 IP 또는 AWS 고정 IP 적용
BROKER_IP = "192.168.0.10"  # 실제 브로커 IP로 수정
BROKER_PORT = 1883
NODE_NAME = "RPi2"          # RPi 3에서 구동 시 "RPi3"로 변경

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"MQTT 브로커({BROKER_IP}) 연결 성공!")
        topic = "photo_booth/status"
        payload = f"{NODE_NAME}_READY"
        client.publish(topic, payload)
        print(f"메시지 전송 성공: [{topic}] -> {payload}")
    else:
        print(f" 연결 실패 (오류 코드: {rc})")

client = mqtt.Client(f"Client_{NODE_NAME}")
client.on_connect = on_connect

try:
    print(f" {BROKER_IP}:{BROKER_PORT} 에 연결 시도 중...")
    client.connect(BROKER_IP, BROKER_PORT, 60)
    client.loop_start()
    time.sleep(2)
    client.loop_stop()
    client.disconnect()
except Exception as e:
    print(f" MQTT 연결 오류: {e}")