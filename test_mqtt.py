# test_mqtt.py
import paho.mqtt.client as mqtt
import time

# -------------------------------------------------------------
# [설정] 추후 AWS 고정 IP 적용 시 이 부분만 변경하세요
BROKER_IP = "192.168.0.10"   # 현재 RPi 1(브로커) 로컬 IP 또는 AWS IP
BROKER_PORT = 1883
NODE_NAME = "RPi2"          # RPi 3일 경우 "RPi3"로 변경
# -------------------------------------------------------------

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ MQTT 브로커({BROKER_IP}) 연결 성공!")
        # 연결 성공 시 준비 완료 메시지 발행
        topic = "photo_booth/status"
        payload = f"{NODE_NAME}_READY"
        client.publish(topic, payload)
        print(f"📡 메시지 전송 완료: [{topic}] -> {payload}")
    else:
        print(f"❌ 연결 실패 (오류 코드: {rc})")

client = mqtt.Client(f"Node_{NODE_NAME}")
client.on_connect = on_connect

try:
    print(f"🔄 {BROKER_IP}:{BROKER_PORT} 에 연결 시도 중...")
    client.connect(BROKER_IP, BROKER_PORT, 60)
    client.loop_start()
    
    # 3초간 유지 후 종료
    time.sleep(3)
    client.loop_stop()
    client.disconnect()
    
except Exception as e:
    print(f"❌ MQTT 통신 예외 발생: {e}")