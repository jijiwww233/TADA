"""Let in-process ASGI tests run where loopback TCP (127.0.0.1) is blocked.

Why: on Windows, Python has no native socketpair, so every asyncio event loop
builds its internal "self-pipe" (used to wake the loop from other threads) out of
a loopback TCP connection. In sandboxes that block 127.0.0.1, creating the loop
hangs forever -- so TestClient / httpx.ASGITransport hang even though they never
start a real server.

Fix (tests only): a ProactorEventLoop that skips the self-pipe and instead wakes
itself by posting a packet to its own IOCP port. IocpProactor._poll() ignores
packets whose OVERLAPPED it doesn't know, so the post just makes it return.

Used only when loopback is actually unavailable; normal machines keep the
default event loop.
"""
import asyncio
import socket
import sys


def loopback_available(timeout: float = 2.0) -> bool:
    lsock = socket.socket()
    csock = socket.socket()
    try:
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        lsock.settimeout(timeout)
        csock.setblocking(False)
        csock.connect_ex(lsock.getsockname())
        conn, _ = lsock.accept()
        conn.close()
        return True
    except OSError:  # includes socket.timeout / PermissionError
        return False
    finally:
        lsock.close()
        csock.close()


if sys.platform == "win32":
    import _overlapped

    class _NoFd:
        def fileno(self):
            return -1  # signal.set_wakeup_fd(-1) == no wakeup fd

    class IocpWakeupLoop(asyncio.ProactorEventLoop):
        def _make_self_pipe(self):
            self._ssock = None
            self._csock = _NoFd()
            # GetQueuedCompletionStatus() treats a NULL OVERLAPPED as an error,
            # so wakeup packets carry the address of a dummy one we keep alive.
            self._wakeup_ov = _overlapped.Overlapped(0)

        def _close_self_pipe(self):
            self._ssock = self._csock = None

        def _loop_self_reading(self, f=None):
            pass  # nothing to read; wakeups arrive as IOCP packets

        def _write_to_self(self):
            iocp = getattr(self._proactor, "_iocp", None)
            if iocp is None:  # proactor already closed
                return
            try:
                _overlapped.PostQueuedCompletionStatus(iocp, 0, 0, self._wakeup_ov.address)
            except OSError:
                pass


def asgi_backend_options() -> dict:
    """anyio backend_options for TestClient; {} means default event loop."""
    if sys.platform == "win32" and not loopback_available():
        return {"loop_factory": IocpWakeupLoop}
    return {}
