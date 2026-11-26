"""키오스크를 IPv4·IPv6 모두로 띄운다 (막심 님 kiosk.py는 수정하지 않음)."""
import http.server, runpy, socket, sys

class Dual(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6
    def __init__(self, addr, handler, *a, **k):
        super().__init__(("::", addr[1]), handler, *a, **k)
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

http.server.ThreadingHTTPServer = Dual
sys.argv = ["node1_main/kiosk.py"] + sys.argv[1:]
runpy.run_path("node1_main/kiosk.py", run_name="__main__")
