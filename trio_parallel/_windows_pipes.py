import sys
from typing import TYPE_CHECKING

import trio
from ._windows_cffi import ErrorCodes, peek_pipe_message_left

assert sys.platform == "win32" or not TYPE_CHECKING

DEFAULT_RECEIVE_SIZE = 65536

# Vendored from trio in v0.25.0 under identical MIT/Apache2 license.
# Copyright Contributors to the Trio project.
# It's trio._util.PipeSendStream but modified, so it doesn't need
# internals or abcs with methods we don't use.


class PipeSendChannel:
    """Represents a message stream over a pipe object."""

    def __init__(self, handle: int) -> None:
        # handles are "owned" by multiprocessing.connection.Pipe
        self._handle = handle
        trio.lowlevel.register_with_iocp(handle)

    async def send(self, value: bytes) -> None:
        # we never send empty bytes
        # if not value:
        #     await trio.lowlevel.checkpoint()
        #     return

        try:
            written = await trio.lowlevel.write_overlapped(self._handle, value)
        except BrokenPipeError as ex:
            raise trio.BrokenResourceError from ex
        # By my reading of MSDN, this assert is guaranteed to pass so long
        # as the pipe isn't in nonblocking mode, but... let's just
        # double-check.
        assert written == len(value)


class PipeReceiveChannel:
    """Represents a message stream over a pipe object."""

    def __init__(self, handle: int) -> None:
        # handles are "owned" by multiprocessing.connection.Pipe
        self._handle = handle
        trio.lowlevel.register_with_iocp(handle)

    async def receive(self) -> bytes:
        buffer = bytearray(DEFAULT_RECEIVE_SIZE)
        try:
            received = await self._receive_some_into(buffer)
        except OSError as e:
            if e.winerror != ErrorCodes.ERROR_MORE_DATA:
                raise  # pragma: no cover, real OSError we can't generate
            left = peek_pipe_message_left(self._handle)
            # preallocate memory to avoid an extra copy of very large messages
            newbuffer = bytearray(DEFAULT_RECEIVE_SIZE + left)
            with memoryview(newbuffer) as view:
                view[:DEFAULT_RECEIVE_SIZE] = buffer
                with trio.CancelScope(shield=True):
                    await self._receive_some_into(view[DEFAULT_RECEIVE_SIZE:])
            return newbuffer
        else:
            del buffer[received:]
            return buffer

    async def _receive_some_into(self, buffer):
        try:
            return await trio.lowlevel.readinto_overlapped(self._handle, buffer)
        except BrokenPipeError:
            # Windows raises BrokenPipeError on one end of a pipe
            # whenever the other end closes, regardless of direction.
            # Convert this to EndOfChannel.
            #
            # We are raising an exception, so we don't need to checkpoint,
            # in contrast to PipeReceiveStream.
            raise trio.EndOfChannel
