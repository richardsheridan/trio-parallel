import os
import multiprocessing
import time

from itertools import count
from pickle import dumps, loads, HIGHEST_PROTOCOL
from typing import Optional, Callable

import trio
from outcome import Outcome, capture

from ._abc import WorkerCache, AbstractWorker, BrokenWorkerError

multiprocessing.get_logger()  # to register multiprocessing atexit handler

if os.name == "nt":
    from ._windows_pipes import PipeReceiveChannel, PipeSendChannel

    async def wait(obj):
        return await trio.lowlevel.WaitForSingleObject(obj)

    def asyncify_pipes(receive_handle, send_handle):
        return PipeReceiveChannel(receive_handle), PipeSendChannel(send_handle)


else:
    from ._posix_pipes import FdChannel

    async def wait(fd):
        return await trio.lowlevel.wait_readable(fd)

    def asyncify_pipes(receive_fd, send_fd):
        return FdChannel(receive_fd), FdChannel(send_fd)


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
                worker = self.popleft()
            except IndexError:
                return
            if worker.is_alive():
                self.appendleft(worker)
                return

    def shutdown(self, grace_period):
        unclean = []
        killed = []
        for worker in self:
            worker.shutdown()
        deadline = time.perf_counter() + grace_period
        for worker in self:
            worker.proc.join(deadline - time.perf_counter())
            if worker.proc.exitcode is None:
                worker.kill()
                killed.append(worker.proc)
            elif worker.proc.exitcode:
                unclean.append(worker.proc)
        if unclean or killed:
            for proc in killed:
                proc.join()
            raise BrokenWorkerError(
                f"Graceful shutdown failed: {len(unclean)} nonzero exit codes "
                f"and {len(killed)} forceful terminations.",
                *unclean,
                *killed,
            )


class WorkerSpawnProc(AbstractWorker):
    _proc_counter = count()
    mp_context = multiprocessing.get_context("spawn")

    def __init__(self, idle_timeout, init, retire):
        self._child_recv_pipe, self._send_pipe = self.mp_context.Pipe(duplex=False)
        self._recv_pipe, self._child_send_pipe = self.mp_context.Pipe(duplex=False)
        self._receive_chan, self._send_chan = asyncify_pipes(
            self._recv_pipe.fileno(), self._send_pipe.fileno()
        )
        if init is not None:  # true except on "fork"
            init = dumps(init, protocol=HIGHEST_PROTOCOL)
        if retire is not None:  # true except on "fork"
            retire = dumps(retire, protocol=HIGHEST_PROTOCOL)
        self.proc = self.mp_context.Process(
            target=self._work,
            args=(
                self._child_recv_pipe,
                self._child_send_pipe,
                idle_timeout,
                init,
                retire,
            ),
            name=f"trio-parallel worker process {next(self._proc_counter)}",
            daemon=True,
        )
        self._wait_lock = trio.Lock()  # Efficiently multiplex waits

    @staticmethod
    def _work(recv_pipe, send_pipe, idle_timeout, init, retire):
        import inspect
        import signal

        def handle_job(job):
            fn, args = loads(job)
            ret = fn(*args)
            if inspect.iscoroutine(ret):
                # Manually close coroutine to avoid RuntimeWarnings
                ret.close()
                raise TypeError(
                    "trio-parallel worker expected a sync function, but {!r} appears "
                    "to be asynchronous".format(getattr(fn, "__qualname__", fn))
                )

            return ret

        def safe_dumps(result):
            string_result = capture(dumps, result, protocol=HIGHEST_PROTOCOL)
            try:
                return string_result.value
            except AttributeError:
                return dumps(string_result, protocol=HIGHEST_PROTOCOL)

        # Intercept keyboard interrupts to avoid passing KeyboardInterrupt
        # between processes. (Trio will take charge via cancellation.)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        try:
            if isinstance(init, bytes):  # true except on "fork"
                init = loads(init)
            if isinstance(retire, bytes):  # true except on "fork"
                retire = loads(retire)
            init()
            while recv_pipe.poll(idle_timeout):
                send_pipe.send_bytes(
                    safe_dumps(capture(handle_job, recv_pipe.recv_bytes()))
                )
                if retire():
                    break
        except (BrokenPipeError, EOFError):
            # Graceful shutdown: If the main process closes the pipes, we will
            # observe one of these exceptions and can simply exit quietly.
            # Closing pipes manually fixed some __del__ flakiness in CI
            send_pipe.close()
            recv_pipe.close()
            return
        except BaseException:
            # Ensure BrokenWorkerError raised in the main proc.
            send_pipe.close()
            # recv_pipe must remain open and clear until the main proc closes it.
            try:
                while True:
                    recv_pipe.recv_bytes()
            except EOFError:
                pass
            raise
        else:
            # Clean idle shutdown or retirement: close recv_pipe first to minimize
            # subsequent race.
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

            if self.proc.pid is None:
                await trio.to_thread.run_sync(self.proc.start)
                # XXX: We must explicitly close these after start to see child closures
                self._child_send_pipe.close()
                self._child_recv_pipe.close()

            try:
                await self._send_chan.send(
                    dumps((sync_fn, args), protocol=HIGHEST_PROTOCOL)
                )
            except trio.BrokenResourceError:
                return None

            try:
                result = loads(await self._receive_chan.receive())
            except trio.EndOfChannel:
                self._send_pipe.close()  # edge case: free proc spinning on recv_bytes
                result = await self.wait()  # skip wait in finally block
                raise BrokenWorkerProcessError(
                    "Worker died unexpectedly:", self.proc
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
                # something interrupted a normal result, proc MUST be dying
                with trio.CancelScope(shield=True):
                    await self.wait()  # pragma: no branch

    def is_alive(self):
        # if the proc is alive, there is a race condition where it could be
        # dying. This call reaps zombie children on Unix.
        return self.proc.is_alive()

    def shutdown(self):
        self._send_pipe.close()

    def kill(self):
        if self.proc.pid is None:
            return
        try:
            self.proc.kill()
        except AttributeError:
            self.proc.terminate()

    async def wait(self):
        if self.proc.exitcode is not None:
            return self.proc.exitcode
        if self.proc.pid is None:
            return None  # killed before started
        async with self._wait_lock:
            await wait(self.proc.sentinel)
        # fix a macos race: Trio GH#1296
        self.proc.join()
        # unfortunately join does not return exitcode
        return self.proc.exitcode

    def __del__(self):
        # Avoid __del__ errors on cleanup: Trio GH#174, GH#1767
        # multiprocessing will close them for us
        if hasattr(self, "_send_chan"):  # pragma: no branch
            self._send_chan.detach()
        if hasattr(self, "_receive_chan"):  # pragma: no branch
            self._receive_chan.detach()


WORKER_PROC_MAP = {"spawn": (WorkerSpawnProc, WorkerProcCache)}

_all_start_methods = set(multiprocessing.get_all_start_methods())

if "forkserver" in _all_start_methods:  # pragma: no branch

    class WorkerForkserverProc(WorkerSpawnProc):
        mp_context = multiprocessing.get_context("forkserver")

    WORKER_PROC_MAP["forkserver"] = WorkerForkserverProc, WorkerProcCache

if "fork" in _all_start_methods:  # pragma: no branch

    class WorkerForkProc(WorkerSpawnProc):
        mp_context = multiprocessing.get_context("fork")

        def __init__(self, idle_timeout, init, retire):
            self._init = init
            self._retire = retire
            super().__init__(idle_timeout, None, None)

        def _work(self, recv_pipe, send_pipe, idle_timeout, init, retire):
            self._send_pipe.close()
            self._recv_pipe.close()
            init = self._init
            del self._init
            retire = self._retire
            del self._retire
            super()._work(recv_pipe, send_pipe, idle_timeout, init, retire)

        async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
            if self.proc.pid is None:
                # on fork, doing start() in a thread is racy, and should be
                # fast enough to be considered non-blocking anyway
                self.proc.start()
                # XXX: We must explicitly close these after start to see child closures
                self._child_send_pipe.close()
                self._child_recv_pipe.close()
                del self._init
                del self._retire
            return await super().run_sync(sync_fn, *args)

    WORKER_PROC_MAP["fork"] = WorkerForkProc, WorkerProcCache

del _all_start_methods
