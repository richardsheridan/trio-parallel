import os
import platform
import struct
import abc
from itertools import count
from multiprocessing import get_context
from pickle import dumps, loads

import trio

from ._util import BrokenWorkerError

# How long a process will idle waiting for new work before gives up and exits.
# This should be longer than a thread timeout proportionately to startup time.
IDLE_TIMEOUT = 60 * 10

_proc_counter = count()


class WorkerProcBase(abc.ABC):
    def __init__(self, mp_context=get_context("spawn")):
        # It is almost possible to synchronize on the pipe alone but on Pypy
        # the _send_pipe doesn't raise the correct error on death.
        child_recv_pipe, self._send_pipe = mp_context.Pipe(duplex=False)
        self._recv_pipe, child_send_pipe = mp_context.Pipe(duplex=False)
        self._proc = mp_context.Process(
            target=self._work,
            args=(child_recv_pipe, child_send_pipe),
            name=f"trio-parallel worker process {next(_proc_counter)}",
            daemon=True,
        )
        # keep our own state flag for quick checks
        self._started = False
        self._rehabilitate_pipes()

    @staticmethod
    def _work(recv_pipe, send_pipe):  # pragma: no cover

        import inspect
        import signal
        import outcome

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

        while recv_pipe.poll(IDLE_TIMEOUT):
            # We got a job, and we are "woken"
            fn, args = loads(recv_pipe.recv_bytes())
            # Do the CPU bound work
            result = outcome.capture(coroutine_checker, fn, args)
            # Send result and go back to idling
            send_pipe.send_bytes(dumps(result, protocol=-1))

            del fn
            del args
            del result

    async def run_sync(self, sync_fn, *args):
        try:
            if not self._started:
                await trio.to_thread.run_sync(self._proc.start)
                self._started = True
            await self._send(dumps((sync_fn, args), protocol=-1))
            # noinspection PyTypeChecker
            result = loads(await self._recv())
        except trio.EndOfChannel:
            # Likely the worker died while we were waiting on a pipe
            self.kill()  # NOTE: must reap zombie child elsewhere
            raise BrokenWorkerError(f"{self._proc} died unexpectedly")
        except BaseException:
            # Cancellation or other unknown errors leave the process in an
            # unknown state, so there is no choice but to kill.
            self.kill()  # NOTE: must reap zombie child elsewhere
            raise
        else:
            return result.unwrap()

    def is_alive(self):
        # if the proc is alive, there is a race condition where it could be
        # dying, but the the barrier should be broken at that time. This
        # call reaps zombie children on Unix.
        return self._proc.is_alive()

    def kill(self):
        if not self._started:
            return
        try:
            self._proc.kill()
        except AttributeError:
            # cpython 3.6 has an edge case where if this process holds
            # the semaphore, a wait can timeout and raise an OSError.
            self._proc.terminate()

    @abc.abstractmethod
    async def wait(self):
        pass

    @abc.abstractmethod
    def _rehabilitate_pipes(self):
        pass

    @abc.abstractmethod
    async def _recv(self):
        pass

    @abc.abstractmethod
    async def _send(self, buf):
        pass


class WindowsWorkerProc(WorkerProcBase):
    async def wait(self):
        if not self._started:
            return
        await trio.lowlevel.WaitForSingleObject(self._proc.sentinel)
        return self._proc.exitcode

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
    async def wait(self):
        if not self._started:
            return
        await trio.lowlevel.wait_readable(self._proc.sentinel)
        e = self._proc.exitcode
        if e is not None:
            return e
        else:  # pragma: no cover # to avoid flaky CI, but it happens regularly
            # race on macOS, see comment in trio.Process._wait
            self._proc.join()
            # unfortunately join does not return exitcode
            return self._proc.exitcode

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

    async def run_sync(self, sync_fn, *args):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self._child_monitor)
            result = await super().run_sync(sync_fn, *args)
            nursery.cancel_scope.cancel()
        return result

    async def _child_monitor(self):
        # If this worker dies, raise a catchable error...
        await self.wait()
        # but not if another error or cancel is incoming, those take priority!
        await trio.lowlevel.checkpoint_if_cancelled()
        raise BrokenWorkerError(f"{self._proc} died unexpectedly")


if os.name == "nt":

    class WorkerProc(WindowsWorkerProc):
        pass


else:

    class WorkerProc(PosixWorkerProc):
        pass
