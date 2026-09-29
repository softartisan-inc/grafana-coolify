#!/usr/bin/env python3
"""Harness notification sink: a fake SMTP server and a fake HTTPS proxy for the test Grafana.

Grafana of the bench (harness/edge.py) sends its emails to the SMTP server and its outgoing
HTTPS through the proxy (HTTPS_PROXY): an email is recorded whole, a Telegram notification is
recorded as the `CONNECT api.telegram.org:443` request Grafana opens to deliver it, which the
proxy refuses (502: nothing reaches Telegram and no real bot is needed). Every other CONNECT is
tunnelled: Grafana 13 installs its Prometheus datasource plugin from grafana.com at first start.
One JSON object per line in the log file: {"kind": "email", "rcpt": [...], "data": "..."} or
{"kind": "proxy", "request": "CONNECT host:port HTTP/1.1"}.

Usage: python3 harness/sink.py LOG_FILE (listens on 127.0.10.102:2525 and 127.0.10.102:3128)
"""

import json
import select
import socket
import socketserver
import sys
import threading

SINK_IP = "127.0.10.102"
SMTP_PORT = 2525
PROXY_PORT = 3128
MAX_DATA = 64 * 1024
REFUSED_HOSTS = ("api.telegram.org",)


class Recorder:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def write(self, kind, **fields):
        with self.lock, open(self.path, "a", encoding="utf-8") as log:
            log.write(json.dumps({"kind": kind, **fields}) + "\n")


class SMTPHandler(socketserver.StreamRequestHandler):
    """Just enough ESMTP for Grafana (no TLS, no AUTH): every message is accepted and recorded."""

    recorder = None

    def reply(self, line):
        self.wfile.write((line + "\r\n").encode("ascii"))

    def handle(self):
        self.reply("220 harness-sink ESMTP")
        recipients = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            command = raw.decode("utf-8", "replace").strip()
            verb = command.upper()
            if verb.startswith(("EHLO", "HELO")):
                self.reply("250-harness-sink")
                self.reply("250 8BITMIME")
            elif verb.startswith("RCPT TO:"):
                recipients.append(command[8:].strip().strip("<>"))
                self.reply("250 OK")
            elif verb == "DATA":
                self.reply("354 end data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    line = self.rfile.readline().decode("utf-8", "replace")
                    if line in ("", ".\r\n", ".\n"):
                        break
                    lines.append(line)
                self.recorder.write("email", rcpt=recipients, data="".join(lines)[:MAX_DATA])
                recipients = []
                self.reply("250 queued")
            elif verb == "QUIT":
                self.reply("221 bye")
                return
            else:
                # MAIL FROM, RSET, NOOP...
                self.reply("250 OK")


class ProxyHandler(socketserver.StreamRequestHandler):
    """Records every request line; refuses CONNECT to REFUSED_HOSTS, tunnels the others."""

    recorder = None

    def handle(self):
        request = self.rfile.readline().decode("utf-8", "replace").strip()
        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
            pass
        self.recorder.write("proxy", request=request)
        method, _, rest = request.partition(" ")
        host, _, port = rest.partition(" ")[0].rpartition(":")
        if method != "CONNECT" or host in REFUSED_HOSTS or not port.isdigit():
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        try:
            upstream = socket.create_connection((host, int(port)), timeout=15)
        except OSError:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        self.wfile.flush()
        with upstream:
            tunnel(self.connection, upstream)


def tunnel(client, upstream):
    sockets = [client, upstream]
    while True:
        readable, _, broken = select.select(sockets, [], sockets, 60)
        if broken or not readable:
            return
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            (upstream if source is client else client).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def servers(log_path, ip=SINK_IP, smtp_port=SMTP_PORT, proxy_port=PROXY_PORT):
    """(SMTP server, proxy server), bound but not serving yet."""
    recorder = Recorder(log_path)
    smtp = Server((ip, smtp_port), type("SMTP", (SMTPHandler,), {"recorder": recorder}))
    proxy = Server((ip, proxy_port), type("Proxy", (ProxyHandler,), {"recorder": recorder}))
    return smtp, proxy


def serve(log_path):
    smtp, proxy = servers(log_path)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    smtp.serve_forever()


def read(log_path):
    """Recorded items, oldest first ([] before the first one)."""
    try:
        with open(log_path, encoding="utf-8") as log:
            return [json.loads(line) for line in log if line.strip()]
    except FileNotFoundError:
        return []


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 harness/sink.py LOG_FILE", file=sys.stderr)
        sys.exit(2)
    serve(sys.argv[1])
