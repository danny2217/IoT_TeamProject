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


def _public_base(args) -> str:
    """QR에 들어갈 주소. 모든 주소에서 받을 때(0.0.0.0, ::)는 그 값을 쓰면 폰에서 안 열린다."""
    import os
    import socket

    if args.public_base:
        return args.public_base.rstrip("/")
    if os.environ.get("BOOTH_PUBLIC_BASE"):
        return os.environ["BOOTH_PUBLIC_BASE"].rstrip("/")
    if args.host in ("0.0.0.0", "::", ""):
        return f"http://{socket.gethostname()}.local:{args.port}"
    host = f"[{args.host}]" if ":" in args.host else args.host
    return f"http://{host}:{args.port}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Node 4 네컷 합성, 저장, QR")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="샘플 사진으로 세로·와이드 결과를 만든다")
    demo.add_argument("--out", default=str(ROOT / "samples" / "output"))

    serve = sub.add_parser("serve", help="사진 수신·합성·다운로드 서버")
    serve.add_argument("--host", default="127.0.0.1",
                       help="받을 주소. '::' 이면 IPv4·IPv6 모두 받는다 (핫스팟에서 IPv6만 쓰는 노트북·폰 대비)")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--data", default=str(ROOT / "data"))
    serve.add_argument("--mqtt", action="store_true", help="Node 1 브로커에 연결")
    serve.add_argument("--broker", default=None)
    serve.add_argument("--public-base", default=None,
                       help="QR에 넣을 주소, 예: http://172.20.10.4:5000 "
                            "(없으면 BOOTH_PUBLIC_BASE, 그것도 없으면 이 파이 이름.local)")

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

    service = Node4Service(Store(args.data), public_base=_public_base(args))
    if args.mqtt:
        from node4.mqtt_node import start_mqtt

        try:
            start_mqtt(service, host=args.broker)
        except Exception as exc:
            print(f"[node4] MQTT 연결 실패. HTTP만 계속합니다: {exc}")
    app = create_app(service)
    print(f"Node 4 HTTP  {args.host}:{args.port}  (QR 주소 {service.public_base})")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
