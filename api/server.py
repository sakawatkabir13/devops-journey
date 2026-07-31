import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


COUNTER_FILE = Path("/data/request-count.txt")


def increment_counter():
    COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        current = int(COUNTER_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        current = 0

    current += 1
    COUNTER_FILE.write_text(str(current))
    return current


class RequestHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/health":
            self.send_json(
                200,
                {
                    "status": "healthy",
                    "service": "devops-api",
                },
            )
            return

        if path == "/info":
            request_count = increment_counter()

            self.send_json(
                200,
                {
                    "message": "Hello from the Docker API",
                    "hostname": socket.gethostname(),
                    "requests": request_count,
                },
            )
            return

        self.send_json(
            404,
            {
                "error": "Not found",
                "path": path,
            },
        )


if __name__ == "__main__":
    address = ("0.0.0.0", 5000)
    server = HTTPServer(address, RequestHandler)

    print("API listening on port 5000", flush=True)
    server.serve_forever()
