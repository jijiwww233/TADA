"""Lifecycle for the optional upload-only LAN bridge."""
import ipaddress
import secrets
import socket
from threading import Lock, Thread

import uvicorn

from tools.phone_import import create_bridge, is_lan_address


class PhonePairing:
    def __init__(self, *, usb=False):
        self._usb = usb
        self._lock = Lock()
        self._server = None
        self._thread = None
        self._listener = None
        self._last_url = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._last_url
            if self._usb:
                address = ipaddress.IPv4Address("127.0.0.1")
            else:
                # UDP connect selects a route locally without sending a packet.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                    route.connect(("192.0.2.1", 9))
                    address = ipaddress.IPv4Address(route.getsockname()[0])
                if not is_lan_address(address):
                    raise OSError("No private local address")
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind((str(address), 0))
                listener.listen(32)
                token = secrets.token_urlsafe(24)
                port = listener.getsockname()[1]
                self._server = uvicorn.Server(uvicorn.Config(create_bridge(token), access_log=False, log_level="warning"))
                self._listener = listener
                self._last_url = f"http://{address}:{port}/#{token}"
                self._thread = Thread(target=self._server.run, kwargs={"sockets": [listener]}, daemon=True)
                self._thread.start()
                return self._last_url
            except BaseException:
                listener.close()
                raise

    def stop(self):
        with self._lock:
            if self._server is None:
                return
            self._server.should_exit = True
            self._thread.join(timeout=2)
            if not self._thread.is_alive():
                self._listener.close()
                self._server = self._thread = self._listener = self._last_url = None
