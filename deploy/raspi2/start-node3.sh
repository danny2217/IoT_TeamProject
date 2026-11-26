#!/bin/bash
# raspi2: Node 3(측면 카메라 + LCD). crontab @reboot 과 바탕화면 자동 실행 둘 다 이 파일을 부르므로
# 잠금으로 한 개만 실행한다 (두 개면 같은 MQTT ID라 서로 끊어 버림).
exec 9> /tmp/node3.lock
flock -n 9 || exit 0
sleep 25
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
cd /home/danny2217/IoT_TeamProject || exit 1
mkdir -p /home/danny2217/booth-logs
python3 -u -m vision.node3 --host raspi.local --camera pi --show --node4 http://raspi.local:5000 >> /home/danny2217/booth-logs/node3.log 2>&1
