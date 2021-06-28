import os
import struct

from abc import abstractmethod
from itertools import count
from multiprocessing import get_context, get_all_start_methods
from pickle import dumps, loads, HIGHEST_PROTOCOL
from typing import Optional, Callable

import trio
from outcome import Outcome, capture

from ._abc import WorkerCache, AbstractWorker, BrokenWorkerError


class BrokenWorkerProcessError(BrokenWorkerError):
    """Raised when a worker process fails or dies unexpectedly.

    This error is not typically encountered in normal use, and indicates a severe
    failure of either trio-parallel or the code that was executing in the worker.

    The last argument of the exception is the underlying
    :class:`multiprocessing.Process` which may be inspected for e.g. exit codes.
    """


class WorkerProcCache(WorkerCache):
    def prune(self):
        # remove procs that have died from the idle timeout
        while True:
            try:
                proc = self.popleft()
            except IndexError:
                return
            if proc.is_alive():
                self.appendleft(proc)
                return

    async def clear(self):
        unclean = []
        killed = []

        async def clean_wait(proc):
            if await proc.wait():
                unclean.append(proc._proc)

        async with trio.open_nursery() as nursery:
            nursery.cancel_scope.shield = True
            nursery.cancel_scope.deadline = trio.current_time() + 10
            # Should have private, single-threaded access to self here
            for proc in self:
                proc._send_pipe.close()
                nursery.start_soon(clean_wait, proc)
        if nursery.cancel_scope.cancelled_caught:
            async with trio.open_nursery() as nursery:
                nursery.cancel_scope.shield = True
                for proc in self:
                    if proc.is_alive():
                        proc.kill()
                        killed.append(proc._proc)
                        nursery.start_soon(proc.wait)
        if unclean or killed:
            raise BrokenWorkerProcessError(
                f"Graceful shutdown failed: {len(unclean)} nonzero exit codes "
                f"and {len(killed)} forceful terminations.",
                *unclean,
                *killed,
            )


class WorkerProcBase(AbstractWorker):
    _proc_counter = count()
    mp_context = get_context("spawn")

    def __init__(self, idle_timeout, retire):
        self._child_recv_pipe, self._send_pipe = self.mp_context.Pipe(duplex=False)
        self._recv_pipe, self._child_send_pipe = self.mp_context.Pipe(duplex=False)
        if retire is not None:  # true except on "fork"
            retire = dumps(retire, protocol=HIGHEST_PROTOCOL)
        self._proc = self.mp_context.Process(
            target=self._work,
            args=(
                self._child_recv_pipe,
                self._child_send_pipe,
                idle_timeout,
                retire,
            ),
            name=f"trio-parallel worker process {next(self._proc_counter)}",
            daemon=True,
        )
        self._started = trio.Event()  # Allow waiting before startup
        self._wait_lock = trio.Lock()  # Efficiently multiplex waits
        self._rehabilitate_pipes()

    @staticmethod
    def _work(recv_pipe, send_pipe, idle_timeout, retire):
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
            if isinstance(retire, bytes):  # true except on "fork"
                retire = loads(retire)
            while not retire() and recv_pipe.poll(idle_timeout):
                fn, args = loads(recv_pipe.recv_bytes())
                # Do the CPU bound work
                result = capture(coroutine_checker, fn, args)
                # Send result and go back to idling
                send_pipe.send_bytes(dumps(result, protocol=HIGHEST_PROTOCOL))

                del fn
                del args
                del result
        except (BrokenPipeError, EOFError):
            # If the main process closes the pipes, we will
            # observe one of these exceptions and can simply exit quietly.
            # Closing pipes manually fixes some __del__ flakiness in CI
            send_pipe.close()
            recv_pipe.close()
            return
        except BaseException as exc:
            # Ensure BrokenWorkerError raised in the main proc.
            send_pipe.close()
            # recv_pipe must remain open and clear until the main proc hits _send().
            try:
                while True:
                    recv_pipe.recv_bytes()
            except EOFError:
                raise exc from None
        else:
            # Clean shutdown: close recv_pipe first to minimize subsequent race.
            recv_pipe.close()
            # Race condition: it is possible to sneak a write through in the main process
            # between the while loop predicate and recv_pipe.close(). Naively, this would
            # make a clean shutdown look like a broken worker. By sending a sentinel
            # value, we can indicate to a waiting main process that we have hit this
            # race condition and need a restart. However, the send MUST be non-blocking
            # to free this process's resources in a timely manner. Therefore, this message
            # can be any size on Windows but must be less than 512 bytes by POSIX.1-2001.
            send_pipe.send_bytes(dumps(None, protocol=HIGHEST_PROTOCOL))
            send_pipe.close()

    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        result = None
        try:

            if not self._started.is_set():
                await trio.to_thread.run_sync(self._proc.start)
                # XXX: We must explicitly close these after start to see child closures
                self._child_send_pipe.close()
                self._child_recv_pipe.close()
                self._started.set()

            try:
                await self._send(dumps((sync_fn, args), protocol=HIGHEST_PROTOCOL))
            except trio.BrokenResourceError:
                return None

            try:
                result = loads(await self._recv())
            except trio.EndOfChannel:
                self._send_pipe.close()  # edge case: free proc spinning on recv_bytes
                result = await self.wait()  # skip kill/wait in finally block
                raise BrokenWorkerProcessError(
                    "Worker died unexpectedly:", self._proc
                ) from None

            return result

        except BaseException:
            # cancellations require kill by contract
            # other exceptions will almost certainly leave us in an
            # unrecoverable state requiring kill as well
            self.kill()
            raise
        finally:  # Convoluted branching, but the concept is simple
            if result is None:  # pragma: no branch
                # that is, if anything interrupted a normal result
                with trio.CancelScope(shield=True):
                    await self.wait()  # pragma: no branch

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
        if self._proc.exitcode is not None:
            return self._proc.exitcode
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
    @abstractmethod
    def _rehabilitate_pipes(self):
        pass

    @abstractmethod
    async def _recv(self):
        pass

    @abstractmethod
    async def _send(self, buf):
        pass

    @abstractmethod
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
        if hasattr(self, "_send_chan"):  # pragma: no branch
            self._send_chan._handle_holder.handle = -1
            self._recv_chan._handle_holder.handle = -1


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
        if size == -1:  # pragma: no cover, can't go this big on CI
            buf = await self._recv_exactly(8)
            (size,) = struct.unpack("!Q", buf)
        return await self._recv_exactly(size)

    async def _recv_exactly(self, size):
        result_bytes = bytearray()
        while size:
            partial_result = await self._recv_stream.receive_some(size)
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

    async def _send(self, buf):
        n = len(buf)
        if n > 0x7FFFFFFF:  # pragma: no cover, can't go this big on CI
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
        if hasattr(self, "_send_stream"):  # pragma: no branch
            self._send_stream._fd_holder.fd = -1
            self._recv_stream._fd_holder.fd = -1


WORKER_PROC_MAP = {}

_all_start_methods = set(get_all_start_methods())

if "spawn" in _all_start_methods:  # pragma: no branch

    if os.name == "nt":

        class WorkerSpawnProc(WindowsWorkerProc):
            pass

    else:

        class WorkerSpawnProc(PosixWorkerProc):
            pass

    WORKER_PROC_MAP["spawn"] = WorkerSpawnProc, WorkerProcCache

if "forkserver" in _all_start_methods:  # pragma: no branch

    class WorkerForkserverProc(PosixWorkerProc):
        mp_context = get_context("forkserver")

    WORKER_PROC_MAP["forkserver"] = WorkerForkserverProc, WorkerProcCache

if "fork" in _all_start_methods:  # pragma: no branch

    class WorkerForkProc(PosixWorkerProc):
        mp_context = get_context("fork")

        def __init__(self, idle_timeout, retire):
            self._retire = retire
            super().__init__(idle_timeout, None)

        def _work(self, recv_pipe, send_pipe, idle_timeout, retire):
            self._send_pipe.close()
            self._recv_pipe.close()
            retire = self._retire
            del self._retire
            super()._work(recv_pipe, send_pipe, idle_timeout, retire)

        async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
            if not self._started.is_set():
                # on fork, doing start() in a thread is racy, and should be
                # fast enough to not be considered blocking anyway
                self._proc.start()
                # XXX: We must explicitly close these after start to see child closures
                self._child_send_pipe.close()
                self._child_recv_pipe.close()
                del self._retire
                self._started.set()
            return await super().run_sync(sync_fn, *args)

    WORKER_PROC_MAP["fork"] = WorkerForkProc, WorkerProcCache

del _all_start_methods
