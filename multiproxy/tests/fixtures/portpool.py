"""Ports for tests that start servers and sockets (2026-10-01).

`free_port()` used to ask the operating system for any free TCP port, close it and hand the number out. Two things made that unsteady:
the number was only checked for TCP (the Courier's discovery socket is UDP), and two test runs at once on one device (a verification run
while another is going) both draw from the same pool of ephemeral ports, so one run's server could land on a port the other had just
"found". Now each test process draws from its own block of ports (chosen by its process id), only hands out a number that is free for
both TCP and UDP right now, never the same number twice in a row until its block wraps, and keeps clear of the ports the program itself
uses (8000, 9999, 59595, the local-model engines' 18080-18180).
"""
import os
import socket
import threading

_BLOCK = 300
_FIRST, _BLOCKS = 12000, 66                     # 12000-31799: below both Linux's and Windows' ephemeral ranges
_AVOID = set(range(18000, 18200)) | {8000, 8099, 8110, 9999, 59595}
_lock = threading.Lock()
_next = 0


def _usable(port: int) -> bool:
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        s = socket.socket(socket.AF_INET, kind)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            s2 = socket.socket(socket.AF_INET, kind)
            s2.bind(("", port))                  # the discovery socket listens on every address
            s2.close()
        except OSError:
            return False
    return True


def free_port() -> int:
    global _next
    base = _FIRST + (os.getpid() % _BLOCKS) * _BLOCK
    with _lock:
        for _ in range(_BLOCK):
            port = base + _next % _BLOCK
            _next += 1
            if port not in _AVOID and _usable(port):
                return port
    s = socket.socket()                          # the block is full of busy ports: fall back to the operating system's choice
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
