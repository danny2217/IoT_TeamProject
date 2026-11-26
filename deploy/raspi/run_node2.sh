#!/bin/bash
# Node 2를 계속 살려 둔다: 부팅 직후 웹캠이 아직 안 잡혀 죽거나 도중에 죽으면 5초 뒤 다시 켠다.
cd /home/danny2217/IoT_TeamProject || exit 1
PY=/home/danny2217/IoT_TeamProject/.venv312/bin/python
while true; do
  for i in $(seq 1 30); do [ -e /dev/video0 ] && break; sleep 1; done
  "$PY" -u -m vision.node2 --host 127.0.0.1 --camera 0 --node4 http://127.0.0.1:5000 --web 8080
  echo "[run_node2] node2 종료 (코드 $?) → 5초 뒤 다시 켬 $(date +%T)"
  sleep 5
done
