import json
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class CapturedRequest:
    path: str
    headers: dict
    body: dict


class AnthropicStub:
    def __init__(self):
        self.requests: list[CapturedRequest] = []
        self._replies: deque = deque()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def reply(self, status: int, body: dict, times: int = 1) -> None:
        for _ in range(times):
            self._replies.append((status, body))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length))
                headers = {name.lower(): value for name, value in self.headers.items()}
                stub.requests.append(CapturedRequest(self.path, headers, body))
                status, reply = stub._replies.popleft()
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("request-id", reply.get("request_id") or "req_stub")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                return None

        return Handler
