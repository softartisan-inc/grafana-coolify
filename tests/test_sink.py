import smtplib
import socket
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from support import ROOT

sys.path.insert(0, str(ROOT / "harness"))
import sink  # noqa: E402


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(1024))


class SinkTest(unittest.TestCase):
    """harness/sink.py: what the test Grafana sends by email or towards Telegram is observable."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "messages.jsonl"
        self.started = list(sink.servers(self.log, "127.0.0.1", 0, 0))
        self.started.append(socketserver.TCPServer(("127.0.0.1", 0), Echo))
        for server in self.started:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.smtp, self.proxy, self.echo = (server.server_address[1] for server in self.started)

    def tearDown(self):
        for server in self.started:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def connect(self, target):
        client = socket.create_connection(("127.0.0.1", self.proxy), timeout=10)
        client.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return client

    def test_email_is_recorded(self):
        with smtplib.SMTP("127.0.0.1", self.smtp, timeout=10) as client:
            client.sendmail("grafana@gc.test", ["ops@gc.test", "dev@gc.test"], "Subject: [FIRING:1] Rejets\r\n\r\nbody\r\n")
        items = sink.read(self.log)
        self.assertEqual([item["kind"] for item in items], ["email"])
        self.assertEqual(items[0]["rcpt"], ["ops@gc.test", "dev@gc.test"])
        self.assertIn("[FIRING:1] Rejets", items[0]["data"])

    def test_telegram_is_recorded_and_refused(self):
        with self.connect("api.telegram.org:443") as client:
            self.assertTrue(client.recv(1024).startswith(b"HTTP/1.1 502"))
        self.assertEqual(sink.read(self.log), [{"kind": "proxy", "request": "CONNECT api.telegram.org:443 HTTP/1.1"}])

    def test_other_hosts_are_tunnelled(self):
        with self.connect(f"127.0.0.1:{self.echo}") as client:
            self.assertTrue(client.recv(1024).startswith(b"HTTP/1.1 200"))
            client.sendall(b"ping")
            self.assertEqual(client.recv(1024), b"ping")

    def test_read_before_anything_is_empty(self):
        self.assertEqual(sink.read(Path(self.tmp.name) / "absent.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
