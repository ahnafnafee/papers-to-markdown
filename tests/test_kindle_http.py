"""Real HTTP exercise of the sender against a bounded local Amazon fixture."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from kindle_delivery.client import KindleClient, WebTransport
from kindle_delivery.files import inspect_epub
from kindle_delivery.state import Receipts
from test_kindle import epub


@unittest.skipUnless(sync_playwright, "Run with the isolated Kindle environment to exercise HTTP.")
class HttpTests(unittest.TestCase):
    def test_actual_http_serialization_and_separate_upload_cookie_jar(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if self.path == "/sendtokindle/init":
                    response = json.dumps({"uploadUrl": address + "/upload", "stkToken": "fixture-upload-token"}).encode()
                elif self.path == "/sendtokindle/send-v2":
                    response = b""
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        address = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
                root = Path(directory)
                book = inspect_epub(epub(root / "paper.epub"))
                request = playwright.request.new_context(storage_state={"cookies": [{
                    "name": "fixture-session", "value": "local-only", "domain": "127.0.0.1", "path": "/",
                    "expires": -1, "httpOnly": True, "secure": False, "sameSite": "Lax"}], "origins": []})
                page = Mock()
                page.locator.return_value.first.input_value.return_value = "fixture-csrf"
                transport = WebTransport(playwright, SimpleNamespace(request=request), page)
                try:
                    with patch("kindle_delivery.client.HOME", address + "/sendtokindle"), \
                         patch("kindle_delivery.client.ORIGIN", address), \
                         patch("kindle_delivery.client.validate_upload_url", side_effect=lambda url: url):
                        self.assertEqual(KindleClient(transport).send(book, Receipts(root, "fixture-account")), "accepted")
                finally:
                    transport.close()
                    request.dispose()
                self.assertEqual([c[0] for c in calls], ["/sendtokindle/init", "/upload", "/sendtokindle/send-v2"])
                self.assertIn("local-only", calls[0][1].get("cookie", ""))
                self.assertEqual(calls[0][1]["anti-csrftoken-a2z"], "fixture-csrf")
                self.assertNotIn("cookie", calls[1][1])
                self.assertNotIn("anti-csrftoken-a2z", calls[1][1])
                self.assertEqual(calls[1][2], book.read_verified())
                self.assertEqual(json.loads(calls[2][2])["title"], "Learning & α")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
