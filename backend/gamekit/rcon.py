"""Minimal Source RCON client (gamekit adapter #1).

Minecraft's Java-edition image (itzg/minecraft-server) speaks the Source RCON
protocol on a loopback-only TCP port. This is a dependency-free client — stdlib
``socket`` + ``struct`` only — so ``gamekit`` stays a pure, extractable package
(plan 53 D1). The panel talks to RCON server-side (D5); the port is never exposed.

Protocol (little-endian): each packet is

    int32 length  (= 4 + 4 + len(body) + 2)
    int32 request_id
    int32 type
    body          (ASCII, NUL-terminated)
    NUL           (empty-string terminator)

Types: 3 = AUTH (login), 2 = AUTH_RESPONSE / EXECCOMMAND, 0 = RESPONSE_VALUE.
A failed auth replies with request_id == -1.

The socket is injectable (``sock`` / ``sock_factory``) so the client can be unit
tested against a scripted fake server with no real network.
"""
import socket
import struct

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(Exception):
    """Any RCON transport/protocol failure."""


class RconAuthError(RconError):
    """The RCON password was rejected (auth reply id == -1)."""


def encode_packet(req_id, req_type, body):
    """Serialize one RCON packet (exposed for tests / fake servers)."""
    payload = struct.pack('<ii', req_id, req_type) + body.encode('utf-8') + b'\x00\x00'
    return struct.pack('<i', len(payload)) + payload


class RconClient:
    def __init__(self, host='127.0.0.1', port=25575, password='', timeout=5.0,
                 sock=None, sock_factory=None):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock = sock                    # a pre-connected socket (tests)
        self._sock_factory = sock_factory or socket.socket
        self._req_id = 0
        self._authed = False

    # -- connection lifecycle -------------------------------------------------

    def connect(self):
        if self._sock is None:
            self._sock = self._sock_factory(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))
        return self

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._authed = False

    def __enter__(self):
        self.connect()
        self.authenticate()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- framing --------------------------------------------------------------

    def _recv_exactly(self, n):
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise RconError('connection closed while reading RCON packet')
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    def _read_packet(self):
        raw_len = self._recv_exactly(4)
        (length,) = struct.unpack('<i', raw_len)
        if length < 10 or length > 4096 * 1024:
            raise RconError(f'implausible RCON packet length {length}')
        payload = self._recv_exactly(length)
        req_id, req_type = struct.unpack('<ii', payload[:8])
        body = payload[8:-2].decode('utf-8', errors='replace')  # strip the two NULs
        return req_id, req_type, body

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def _send(self, req_type, body):
        if self._sock is None:
            raise RconError('not connected')
        rid = self._next_id()
        self._sock.sendall(encode_packet(rid, req_type, body))
        return rid

    # -- operations -----------------------------------------------------------

    def authenticate(self):
        """Log in with the RCON password. Raises RconAuthError on rejection."""
        sent_id = self._send(SERVERDATA_AUTH, self.password)
        req_id, req_type, _ = self._read_packet()
        # Some servers emit an empty RESPONSE_VALUE before the auth reply.
        if req_type == SERVERDATA_RESPONSE_VALUE:
            req_id, req_type, _ = self._read_packet()
        if req_id == -1:
            raise RconAuthError('RCON authentication failed (bad password)')
        if req_id != sent_id:
            raise RconError('RCON auth response id mismatch')
        self._authed = True
        return self

    def command(self, cmd):
        """Run *cmd* and return the server's response body."""
        if not self._authed:
            raise RconError('RCON command before successful authenticate()')
        sent_id = self._send(SERVERDATA_EXECCOMMAND, cmd)
        req_id, req_type, body = self._read_packet()
        if req_id != sent_id:
            raise RconError('RCON response id mismatch')
        return body
