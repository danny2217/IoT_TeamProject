#!/bin/bash
# 2대 데모(raspi = Node 1·2·4 겸용) 부팅 스크립트. crontab: @reboot ~/booth_up.sh > ~/booth-logs/boot.log 2>&1
# node1_main/ 은 막심 님 브랜치(feature/mqtt-arduino-sensor)를 raspi에 복사해 둔 것, kiosk6.py·run_node2.sh는 홈(~)에 둔다.
# raspi: MQTT(1883)가 뜰 때까지 기다린 뒤 포토부스 프로그램을 켠다. 이미 켜진 건 건너뜀.
# 진행 화면(8000)·Node 4(5000)는 IPv4+IPv6 둘 다 받게 켠다 (맥북 접속용).
for i in $(seq 1 60); do ss -ltn | grep -q ":1883 " && break; sleep 2; done
cd /home/danny2217/IoT_TeamProject || exit 1
mkdir -p /home/danny2217/booth-logs
PY=/home/danny2217/IoT_TeamProject/.venv312/bin/python
L=/home/danny2217/booth-logs
start() { setsid nohup "$@" < /dev/null >> "$LOG" 2>&1 & }
pkill -f "m node4 serve --host 0.0.0.0"
pkill -f "^$PY /home/danny2217/IoT_TeamProject/node1_main/kiosk.py"
sleep 1
pgrep -f "m node4 serve --host ::" > /dev/null || { LOG=$L/node4.log;   start $PY -u -m node4 serve --host :: --port 5000 --mqtt --broker 127.0.0.1 --public-base http://raspi.local:5000; }
pgrep -f "session_manager.py"      > /dev/null || { LOG=$L/session.log; start $PY -u node1_main/session_manager.py --host 127.0.0.1 --no-arduino; }
pgrep -f "kiosk6.py"               > /dev/null || { LOG=$L/kiosk.log;   start $PY -u /home/danny2217/kiosk6.py --host 127.0.0.1 --port 8000; }
pgrep -f "run_node2.sh"            > /dev/null || { LOG=$L/node2.log;   start /home/danny2217/run_node2.sh; }
