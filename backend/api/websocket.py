"""Minimal RFC 6455 server support using only the Python standard library."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import struct
from typing import Any

LOG = logging.getLogger(__name__)


def accept_key(value: str) -> str:
    digest = hashlib.sha1((value + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    return base64.b64encode(digest).decode()


def frame(payload: str, opcode: int = 1) -> bytes:
    body = payload.encode()
    length = len(body)
    header = bytes([0x80 | opcode])
    if length < 126:
        return header + bytes([length]) + body
    if length < 65536:
        return header + bytes([126]) + struct.pack("!H", length) + body
    return header + bytes([127]) + struct.pack("!Q", length) + body


def read_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    first = sock.recv(2)
    if len(first) != 2:
        return None
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    if length > 65536:
        raise ValueError("WebSocket frame exceeds 64 KiB limit")
    mask = sock.recv(4) if masked else b""
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(min(65536, length - len(data)))
        if not chunk:
            return None
        data.extend(chunk)
    if masked:
        for index in range(length):
            data[index] ^= mask[index % 4]
    return opcode, bytes(data)


def send_json(sock: socket.socket, event: dict[str, Any]) -> None:
    sock.sendall(frame(json.dumps(event, separators=(",", ":"))))

