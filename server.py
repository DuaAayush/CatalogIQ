"""
server.py  ->  Yahin se project start hota hai:   python server.py

Ye sirf HTTP ka kaam karta hai:
  - browser/curl ki request padhna
  - /api/... wali requests api.py ko dena
  - / aur /static/... par HTML/CSS/JS files dena
"""
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from api import Api
from config import load_settings
from database import Database
from pipeline import Pipeline

STATIC_DIR = Path(__file__).parent / "static"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # connection reuse, isliye Content-Length zaroori hai
    api = None                              # make_server() yahan Api rakhta hai

    def do_GET(self):
        self.run("GET")

    def do_POST(self):
        self.run("POST")

    def do_PATCH(self):
        self.run("PATCH")

    def do_PUT(self):
        self.run("PUT")

    def do_DELETE(self):
        self.run("DELETE")

    def run(self, method):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if method == "GET" and (url.path == "/" or url.path.startswith("/static/")):
            self.send_static_file(url.path)
            return
        status, payload = self.api.handle(method, url.path, parse_qs(url.query, keep_blank_values=True), body)
        self.send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json")

    def send_static_file(self, url_path):
        name = "index.html" if url_path == "/" else url_path[len("/static/"):]
        file = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in file.parents or not file.is_file():   # bahar ki files nahi dena
            self.send_bytes(404, json.dumps({"error": "not found"}).encode(), "application/json")
            return
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        self.send_bytes(200, file.read_bytes(), content_type + "; charset=utf-8" if content_type.startswith("text/") or content_type.endswith("javascript") else content_type)

    def send_bytes(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass                                # har request ka log nahi chahiye


class Server(ThreadingHTTPServer):
    daemon_threads = True                   # har request ka apna thread
    request_queue_size = 128


def make_server(api, host, port):
    handler = type("BoundHandler", (Handler,), {"api": api})
    return Server((host, port), handler)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    database = Database(settings.db_path)
    database.create_tables()
    pipeline = Pipeline(settings, database)

    resumed = pipeline.start_unfinished_jobs()      # crash recovery
    if resumed:
        logging.info("Adhoore kaam dobara shuru: %d items", resumed)

    server = make_server(Api(pipeline, database, settings), "0.0.0.0", settings.port)
    print("CatalogIQ chal raha hai: http://localhost:%d  (LLM_PROVIDER=%s, LLM_CONCURRENCY=%d)"
          % (settings.port, settings.llm_provider, settings.llm_concurrency))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBand ho raha hai...")
    finally:
        pipeline.stop()
        server.server_close()


if __name__ == "__main__":
    main()
