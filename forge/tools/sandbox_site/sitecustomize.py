"""Loaded automatically inside the sandbox: blocks non-loopback socket connections.

Only covers Python code. Subprocesses written in other languages are not blocked.
"""

import os
import socket

if os.environ.get("FORGE_SANDBOX") == "1":
    _LOOPBACK = ("127.", "::1", "localhost")

    def _allowed(address):
        if isinstance(address, (str, bytes)):  # AF_UNIX path
            return True
        host = str(address[0])
        return host.startswith(_LOOPBACK)

    def _guard(fn):
        def wrapper(self, address, *args, **kwargs):
            if not _allowed(address):
                raise PermissionError(f"network access blocked by Forge sandbox: {address!r}")
            return fn(self, address, *args, **kwargs)

        return wrapper

    socket.socket.connect = _guard(socket.socket.connect)
    socket.socket.connect_ex = _guard(socket.socket.connect_ex)
