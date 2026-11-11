from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import threading
import tempfile
import unittest
from pathlib import Path

from backend.api.server import AegisServer
from backend.api.websocket import accept_key
from backend.configuration.settings import Settings


class Step1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = AegisServer(Settings(port=0, data_dir=cls.temp.name,
            knowledge_root=str(Path(cls.temp.name) / "knowledge"),
            static_dir=str(Path(__file__).parents[1] / "frontend")))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def http(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        headers = {"Content-Type": "application/json"} if body else {}
        headers["Authorization"] = "Bearer " + self.server.api_token
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_application_startup_and_health(self) -> None:
        status, body = self.http("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_frontend_is_restored(self) -> None:
        status, body = self.http("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"AEGIS", body)
        self.assertIn(b"visualizer", body)

    def test_invalid_requests_and_no_fake_response(self) -> None:
        status, _ = self.http("GET", "/missing")
        self.assertEqual(status, 404)
        status, body = self.http("POST", "/api/messages", b"{bad")
        self.assertEqual(status, 400)
        status, body = self.http("POST", "/api/messages", json.dumps({"message": "hello"}).encode())
        self.assertEqual(status, 501)
        self.assertEqual(json.loads(body)["error"], "inference_not_configured")

    def test_memory_http_controls(self) -> None:
        status, body = self.http("POST", "/api/memory", json.dumps({
            "category": "explicit", "content": "HTTP memory test", "explicit": True,
        }).encode())
        self.assertEqual(status, 201)
        memory_id = json.loads(body)["memory_id"]
        status, body = self.http("GET", "/api/memory?q=HTTP%20memory")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["memories"]), 1)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request("DELETE", f"/api/memory/{memory_id}", headers={"Authorization": "Bearer " + self.server.api_token})
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()

    def test_voice_http_controls_report_unavailable_without_model(self) -> None:
        status, body = self.http("GET", "/api/voice/status")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["available"])
        status, body = self.http("PUT", "/api/voice/preferences", json.dumps({"speaking_rate": 1.2, "volume": 0.5}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["speaking_rate"], 1.2)
        status, _ = self.http("POST", "/api/voice/test", b"{}")
        self.assertEqual(status, 503)
        status, body = self.http("POST", "/api/voice/microphone", b'{"enabled":true}')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["microphone_active"])
        self.http("POST", "/api/voice/microphone", b'{"enabled":false}')

    def test_final_integration_safe_routes(self) -> None:
        status, body = self.http("GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertTrue({"system.status", "knowledge.search", "time.now", "file.metadata", "document.read"}.issubset(
            {tool["name"] for tool in json.loads(body)["tools"]}))
        status, body = self.http("POST", "/api/tools/system.status", b"{}")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, body = self.http("POST", "/api/agents/tasks", json.dumps({"instruction": "bounded check"}).encode())
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["status"], "completed")

    def test_websocket_connection_disconnection_and_message_delivery(self) -> None:
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET /api/ws HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"Sec-WebSocket-Protocol: aegis, bearer.{self.server.api_token}\r\n\r\n").encode()
        sock.sendall(request)
        response = self._read_until(sock, b"\r\n\r\n")
        self.assertIn(b"101 Switching Protocols", response)
        self.assertIn(accept_key(key).encode(), response)
        connected = self._read_frame(sock)
        self.assertEqual(connected["type"], "client.connected")
        self._send_frame(sock, {"type": "conversation.message", "request_id": "r1", "payload": {"message": "hello"}})
        first = self._read_frame(sock)
        second = self._read_frame(sock)
        self.assertEqual([first["type"], second["type"]], ["conversation.response.started", "conversation.response.failed"])
        self.assertEqual(second["payload"]["error"], "inference_not_configured")
        self._send_frame(sock, {"type": "conversation.message", "request_id": "r1", "payload": {"message": "hello"}})
        duplicate = self._read_frame(sock)
        self.assertEqual(duplicate["payload"]["error"], "duplicate_request")
        sock.close()

    @staticmethod
    def _read_until(sock: socket.socket, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            data += sock.recv(1)
        return data

    @staticmethod
    def _send_frame(sock: socket.socket, value: dict) -> None:
        body = json.dumps(value).encode()
        mask = b"test"
        encoded = bytes(byte ^ mask[index % 4] for index, byte in enumerate(body))
        sock.sendall(bytes([0x81, 0x80 | len(body)]) + mask + encoded)

    @staticmethod
    def _read_frame(sock: socket.socket) -> dict:
        header = sock.recv(2)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", sock.recv(2))[0]
        body = sock.recv(length)
        return json.loads(body)


if __name__ == "__main__":
    unittest.main()

