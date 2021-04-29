import os
import struct
import abc
from itertools import count
from multiprocessing import get_context
from pickle import dumps, loads
from typing import Optional

import trio
from outcome import Outcome, capture


class BrokenWorkerError(RuntimeError):
    """Raised when a worker process fails or dies unexpectedly.

    This error is not typically encountered in normal use, and indicates a severe
    failure of either trio-parallel or the code that was executing in the worker.

    The last argument of the exception is the underlying
    :class:`multiprocessing.Process` which may be inspected for e.g. exit codes.
    """

    pass


# How long a process will idle waiting for new work before gives up and exits.
# This should be longer than a thread timeout proportionately to startup time.
IDLE_TIMEOUT = 60 * 10

_proc_counter = count()


class WorkerProcBase(abc.ABC):
    def __init__(self, mp_context=get_context("spawn")):
        self._child_recv_pipe, self._send_pipe = mp_context.Pipe(duplex=False)
        self._recv_pipe, self._child_send_pipe = mp_context.Pipe(duplex=False)
        self._proc = mp_context.Process(
            target=self._work,
            args=(self._child_recv_pipe, self._child_send_pipe),
            name=f"trio-parallel worker process {next(_proc_counter)}",
            daemon=True,
        )
        self._started = trio.Event()  # Allow waiting before startup
        self._wait_lock = trio.Lock()  # Efficiently multiplex waits
        self._rehabilitate_pipes()

    @staticmethod
    def _work(recv_pipe, send_pipe):  # pragma: no cover

        import inspect
        import signal

        def coroutine_checker(fn, args):
            ret = fn(*args)
            if inspect.iscoroutine(ret):
                # Manually close coroutine to avoid RuntimeWarnings
                ret.close()
                raise TypeError(
                    "trio-parallel worker expected a sync function, but {!r} appears "
                    "to be asynchronous".format(getattr(fn, "__qualname__", fn))
                )

            return ret

        # Intercept keyboard interrupts to avoid passing KeyboardInterrupt
        # between processes. (Trio will take charge via cancellation.)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        try:
            while recv_pipe.poll(IDLE_TIMEOUT):
                fn, args = loads(recv_pipe.recv_bytes())
                # Do the CPU bound work
                result = capture(coroutine_checker, fn, args)
                # Send result and go back to idling
                send_pipe.send_bytes(dumps(result, protocol=-1))

                del fn
                del args
                del result
        except (BrokenPipeError, EOFError):
            # If the main process ends but this one is still alive, we will
            # observe one of these exceptions and can simply exit quietly.
            return

        # Clean idle shutdown: close recv_pipe first to minimize subsequent race.
        recv_pipe.close()
        # Race condition: it is possible to sneak a write through in the main process
        # between the recv_pipe poll timeout and close. Naively, this would
        # make a clean shutdown look like a broken worker. By sending a sentinel
        # value, we can indicate to a waiting main process that we have hit this
        # race condition and need a restart. However, the send MUST be non-blocking
        # to free this process's resources in a timely manner. Therefore, this message
        # can be any size on Windows but must be less than 512 bytes by POSIX.1-2001.
        send_pipe.send_bytes(dumps(None, protocol=-1))
        send_pipe.close()

    async def run_sync(self, sync_fn, *args) -> Optional[Outcome]:
        result = None
        try:

            if not self._started.is_set():
                await trio.to_thread.run_sync(self._proc.start)
                # XXX: We must explicitly close these after start to see child closures
                self._child_send_pipe.close()
                self._child_recv_pipe.close()
                self._started.set()

            try:
                await self._send(dumps((sync_fn, args), protocol=-1))
            except trio.BrokenResourceError:
                return None

            try:
                result = loads(await self._recv())
            except trio.EndOfChannel:
                result = await self.wait()  # skip kill/wait in finally block
                raise BrokenWorkerError(
                    "Worker died unexpectedly:", self._proc
                ) from None

            return result

        finally:  # pragma: no cover convoluted branching, but the concept is simple
            if result is None:  # that is, if anything interrupted a normal result
                self.kill()  # maybe redundant
                # do cleanup soon, but no need to block here
                trio.lowlevel.spawn_system_task(self.wait)

    def is_alive(self):
        # if the proc is alive, there is a race condition where it could be
        # dying. This call reaps zombie children on Unix.
        return self._proc.is_alive()

    def kill(self):
        if self._proc.pid is None:
            self._started.set()  # unblock self.wait()
            return
        try:
            self._proc.kill()
        except AttributeError:
            self._proc.terminate()

    async def wait(self):
        await self._started.wait()
        if self._proc.pid is None:
            return None  # killed before started
        async with self._wait_lock:
            await self._wait()
        # redundant wait, but also fix a macos race and do Process cleanup
        self._proc.join()
        # unfortunately join does not return exitcode
        return self._proc.exitcode

    # platform-specific behavior
    @abc.abstractmethod
    def _rehabilitate_pipes(self):
        pass

    @abc.abstractmethod
    async def _recv(self):
        pass

    @abc.abstractmethod
    async def _send(self, buf):
        pass

    @abc.abstractmethod
    async def _wait(self):
        pass


class WindowsWorkerProc(WorkerProcBase):
    async def _wait(self):
        await trio.lowlevel.WaitForSingleObject(self._proc.sentinel)

    def _rehabilitate_pipes(self):
        # These must be created in an async context
        from ._windows_pipes import PipeSendChannel, PipeReceiveChannel

        self._send_chan = PipeSendChannel(self._send_pipe.fileno())
        self._recv_chan = PipeReceiveChannel(self._recv_pipe.fileno())

    async def _recv(self):
        return await self._recv_chan.receive()

    async def _send(self, buf):
        return await self._send_chan.send(buf)

    def __del__(self):
        # Avoid __del__ errors on cleanup: GH#174, GH#1767
        # multiprocessing will close them for us
        if hasattr(self, "_send_chan"):
            self._send_chan._handle_holder.handle = -1
            self._recv_chan._handle_holder.handle = -1
        else:  # pragma: no cover
            pass


class PosixWorkerProc(WorkerProcBase):
    async def _wait(self):
        await trio.lowlevel.wait_readable(self._proc.sentinel)

    def _rehabilitate_pipes(self):
        # These must be created in an async context
        self._send_stream = trio.lowlevel.FdStream(self._send_pipe.fileno())
        self._recv_stream = trio.lowlevel.FdStream(self._recv_pipe.fileno())

    async def _recv(self):
        buf = await self._recv_exactly(4)
        (size,) = struct.unpack("!i", buf)
        if size == -1:  # pragma: no cover # can't go this big on CI
            buf = await self._recv_exactly(8)
            (size,) = struct.unpack("!Q", buf)
        return await self._recv_exactly(size)

    async def _recv_exactly(self, size):
        result_bytes = bytearray()
        while size:
            partial_result = await self._recv_stream.receive_some(size)
            num_recvd = len(partial_result)
            if not num_recvd:
                raise trio.EndOfChannel("got end of file during message")
            result_bytes.extend(partial_result)
            if num_recvd > size:  # pragma: no cover
                raise RuntimeError("Oversized response")
            else:
                size -= num_recvd
        return result_bytes

    async def _send(self, buf):
        n = len(buf)
        if n > 0x7FFFFFFF:  # pragma: no cover # can't go this big on CI
            pre_header = struct.pack("!i", -1)
            header = struct.pack("!Q", n)
            await self._send_stream.send_all(pre_header)
            await self._send_stream.send_all(header)
            await self._send_stream.send_all(buf)
        else:
            # For wire compatibility with 3.7 and lower
            header = struct.pack("!i", n)
            if n > 16384:
                # The payload is large so Nagle's algorithm won't be triggered
                # and we'd better avoid the cost of concatenation.
                await self._send_stream.send_all(header)
                await self._send_stream.send_all(buf)
            else:
                # Issue #20540: concatenate before sending, to avoid delays due
                # to Nagle's algorithm on a TCP socket.
                # Also note we want to avoid sending a 0-length buffer separately,
                # to avoid "broken pipe" errors if the other end closed the pipe.
                await self._send_stream.send_all(header + buf)

    def __del__(self):
        # Avoid __del__ errors on cleanup: GH#174, GH#1767
        # multiprocessing will close them for us
        if hasattr(self, "_send_stream"):
            self._send_stream._fd_holder.fd = -1
            self._recv_stream._fd_holder.fd = -1
        else:  # pragma: no cover
            pass


if os.name == "nt":

    class WorkerProc(WindowsWorkerProc):
        pass


else:

    class WorkerProc(PosixWorkerProc):
        pass
