from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.api.server import AegisServer
from backend.configuration.settings import Settings
from backend.conversation.models import CancellationToken
from backend.inference.base import InferenceRequest, ModelMetadata, ResourceSnapshot


class BlockingProvider:
    metadata = ModelMetadata("test", "blocking", context_window=512)

    def available(self) -> bool:
        return True

    def load(self) -> None: pass
    def unload(self) -> None: pass
    def resources(self) -> ResourceSnapshot: return ResourceSnapshot()

    def stream(self, request: InferenceRequest, cancellation: CancellationToken):
        yield "partial"
        while not cancellation.cancelled:
            time.sleep(0.01)


class WebSocketCancellationTests(unittest.TestCase):
    def test_cancel_event_is_read_while_response_is_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = AegisServer(Settings(port=0, data_dir=directory,
                                         static_dir=str(Path(__file__).parents[1] / "frontend")))
            server.conversations.provider = BlockingProvider()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=3)
            try:
                key = base64.b64encode(b"aegis-websocket-test").decode()
                request = (f"GET /api/ws HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           f"Sec-WebSocket-Protocol: aegis, bearer.{server.api_token}\r\n\r\n").encode()
                sock.sendall(request)
                response = self._read_until(sock, b"\r\n\r\n")
                self.assertIn(b"101 Switching Protocols", response)
                expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
                self.assertIn(expected, response)
                self.assertEqual(self._read_event(sock)["type"], "client.connected")
                self._send_event(sock, {"type": "conversation.message", "request_id": "work-request",
                                        "payload": {"message": "please stream"}})
                self.assertEqual(self._read_event(sock)["type"], "conversation.response.started")
                self.assertEqual(self._read_event(sock)["type"], "conversation.response.chunk")
                self._send_event(sock, {"type": "conversation.response.cancel", "request_id": "cancel-request",
                                        "payload": {"request_id": "work-request"}})
                events = [self._read_event(sock), self._read_event(sock)]
                self.assertEqual({event["type"] for event in events},
                                 {"system.status", "conversation.response.failed"})
                status = next(event for event in events if event["type"] == "system.status")
                failure = next(event for event in events if event["type"] == "conversation.response.failed")
                self.assertTrue(status["payload"]["cancelled"])
                self.assertEqual(failure["payload"]["error"], "cancelled")
            finally:
                sock.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    @staticmethod
    def _read_exact(sock: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            part = sock.recv(count - len(data))
            if not part:
                raise ConnectionError("WebSocket closed early")
            data.extend(part)
        return bytes(data)

    @classmethod
    def _read_until(cls, sock: socket.socket, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            data.extend(cls._read_exact(sock, 1))
        return bytes(data)

    @classmethod
    def _read_event(cls, sock: socket.socket) -> dict[str, object]:
        header = cls._read_exact(sock, 2)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", cls._read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", cls._read_exact(sock, 8))[0]
        return json.loads(cls._read_exact(sock, length))

    @staticmethod
    def _send_event(sock: socket.socket, event: dict[str, object]) -> None:
        payload = json.dumps(event).encode()
        mask = b"test"
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes((0x81, 0x80 | length))
        elif length < 65536:
            header = bytes((0x81, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x81, 0x80 | 127)) + struct.pack("!Q", length)
        sock.sendall(header + mask + masked)


if __name__ == "__main__":
    unittest.main()
