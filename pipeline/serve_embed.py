"""Local stand-in for Workers AI embeddings (dev only): POST {"text": [...]} -> {"data": [[...], ...]}."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .embed import embed


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        vecs = embed(body["text"]).tolist()
        out = json.dumps({"data": vecs}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("embed server on :8788", flush=True)
    HTTPServer(("127.0.0.1", 8788), H).serve_forever()
