import errno
import os
import sys
import struct
from typing import TYPE_CHECKING

import trio

assert not sys.platform == "win32" or not TYPE_CHECKING

DEFAULT_RECEIVE_SIZE = 65536

# Vendored from trio in v0.25.0 under identical MIT/Apache2 license.
# Copyright Contributors to the Trio project.
# It's modified so it doesn't need internals or abcs with methods we don't use.


class FdStream:
    """
    Represents a stream given the file descriptor to a pipe, TTY, etc.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        os.set_blocking(fd, False)

    async def send_all(self, data: bytes) -> None:
        await trio.lowlevel.checkpoint()
        length = len(data)
        # adapted from the SocketStream code
        with memoryview(data) as view:
            sent = 0
            while sent < length:
                with view[sent:] as remaining:
                    try:
                        sent += os.write(self._fd, remaining)
                    except BlockingIOError:
                        await trio.lowlevel.wait_writable(self._fd)
                    except OSError as e:
                        if e.errno == errno.EBADF:  # pragma: no cover, never closed
                            raise trio.ClosedResourceError(
                                "file was already closed"
                            ) from None
                        else:
                            raise trio.BrokenResourceError from e

    async def receive_some(self, max_bytes: int) -> bytes:
        await trio.lowlevel.checkpoint()
        while True:
            try:
                data = os.read(self._fd, max_bytes)
            except BlockingIOError:
                await trio.lowlevel.wait_readable(self._fd)
            except OSError as e:  # pragma: no cover, never closed, impossible error
                if e.errno == errno.EBADF:
                    raise trio.ClosedResourceError("file was already closed") from None
                else:
                    raise trio.BrokenResourceError from e
            else:
                break

        return data


# We copy the wire protocol code from multiprocessing.connection.Connection
# but asyncifiy it with FdStream so as a derivative work this notice is required:
# Copyright © Python Software Foundation; All Rights Reserved
class FdChannel:
    """Represents a message stream over a pipe object."""

    def __init__(self, send_fd):
        self._stream = FdStream(send_fd)

    async def send(self, buf: bytes) -> None:
        n = len(buf)
        if n > 0x7FFFFFFF:  # pragma: no cover, can't go this big on CI
            pre_header = struct.pack("!i", -1)
            header = struct.pack("!Q", n)
            await self._stream.send_all(pre_header)
            await self._stream.send_all(header)
            await self._stream.send_all(buf)
        else:
            # For wire compatibility with multiprocessing Connection 3.7 and lower
            header = struct.pack("!i", n)
            if n > 16384:
                # The payload is large so Nagle's algorithm won't be triggered
                # and we'd better avoid the cost of concatenation.
                await self._stream.send_all(header)
                await self._stream.send_all(buf)
            else:
                # Issue #20540: concatenate before sending, to avoid delays due
                # to Nagle's algorithm on a TCP socket.
                # Also note we want to avoid sending a 0-length buffer separately,
                # to avoid "broken pipe" errors if the other end closed the pipe.
                await self._stream.send_all(header + buf)

    async def receive(self) -> bytes:
        buf = await self._recv_exactly(4)
        (size,) = struct.unpack("!i", buf)
        if size == -1:  # pragma: no cover, can't go this big on CI
            buf = await self._recv_exactly(8)
            (size,) = struct.unpack("!Q", buf)
        return await self._recv_exactly(size)

    async def _recv_exactly(self, size):
        await trio.lowlevel.checkpoint_if_cancelled()
        result_bytes = bytearray()
        while size:
            partial_result = await self._stream.receive_some(size)
            num_recvd = len(partial_result)
            if not num_recvd:
                if not result_bytes:
                    raise trio.EndOfChannel
                else:  # pragma: no cover, edge case from mp.Pipe
                    raise OSError("got end of file during message")
            result_bytes.extend(partial_result)
            if num_recvd > size:  # pragma: no cover, edge case from mp.Pipe
                raise RuntimeError("Oversized response")
            else:
                size -= num_recvd
        await trio.lowlevel.cancel_shielded_checkpoint()
        return result_bytes
