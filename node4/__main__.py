"""Node 4 실행 입구.

  python -m node4 demo
  python -m node4 serve
  python -m node4 serve --mqtt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Node 4 네컷 합성, 저장, QR")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="샘플 사진으로 세로·와이드 결과를 만든다")
    demo.add_argument("--out", default=str(ROOT / "samples" / "output"))

    serve = sub.add_parser("serve", help="사진 수신·합성·다운로드 서버")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--data", default=str(ROOT / "data"))
    serve.add_argument("--mqtt", action="store_true", help="Node 1 브로커에 연결")
    serve.add_argument("--broker", default=None)

    args = parser.parse_args(argv)
    if args.command == "demo":
        from node4.demo import run_demo

        manifest = run_demo(Path(args.out))
        print("샘플 합성이 끝났습니다.")
        for item in manifest["results"]:
            print(f"  {item['file']}  layout={item['layout']}  best={item['best_shot']}")
        print(f"  슬라이드용 이미지: {Path(args.out) / 'slide12_samples.png'}")
        return 0

    from node4.server import create_app
    from node4.service import Node4Service
    from node4.storage import Store

    service = Node4Service(Store(args.data), public_base=f"http://{args.host}:{args.port}")
    if args.mqtt:
        from node4.mqtt_node import start_mqtt

        try:
            start_mqtt(service, host=args.broker)
        except Exception as exc:
            print(f"[node4] MQTT 연결 실패. HTTP만 계속합니다: {exc}")
    app = create_app(service)
    print(f"Node 4 HTTP  http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
