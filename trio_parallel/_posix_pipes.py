import os
import struct
from typing import TYPE_CHECKING

import trio
from trio.abc import Channel

assert os.name != "nt" or not TYPE_CHECKING


class FdChannel(Channel[bytes]):
    """Represents a message stream over a pipe object."""

    def __init__(self, send_fd):
        self._stream = trio.lowlevel.FdStream(send_fd)

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
        result_bytes = bytearray()
        while size:
            partial_result = await self._stream.receive_some(size)
            num_recvd = len(partial_result)
            if not num_recvd:
                if not result_bytes:
                    raise trio.EndOfChannel
                else:  # pragma: no cover
                    raise OSError("got end of file during message")
            result_bytes.extend(partial_result)
            if num_recvd > size:  # pragma: no cover
                raise RuntimeError("Oversized response")
            else:
                size -= num_recvd
        return result_bytes

    def detach(self):
        self._stream._fd_holder.fd = -1

    async def aclose(self):  # pragma: no cover
        return await self._stream.aclose()
