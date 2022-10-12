import sys
import multiprocessing
import time

from itertools import count
from pickle import HIGHEST_PROTOCOL
from typing import Optional, Callable

import trio

try:
    from cloudpickle import dumps, loads
except ImportError:
    from pickle import dumps, loads

from outcome import Outcome, Error
from . import _abc
import _trio_parallel_workers as tp_workers

multiprocessing.get_logger()  # to register multiprocessing atexit handler

if sys.platform == "win32":
    from trio.lowlevel import WaitForSingleObject
    from ._windows_pipes import PipeReceiveChannel, PipeSendChannel

    async def wait(obj):
        return await WaitForSingleObject(obj)

    def asyncify_pipes(receive_handle, send_handle):
        return PipeReceiveChannel(receive_handle), PipeSendChannel(send_handle)

else:
    from trio.lowlevel import wait_readable
    from ._posix_pipes import FdChannel

    async def wait(fd):
        return await wait_readable(fd)

    def asyncify_pipes(receive_fd, send_fd):
        return FdChannel(receive_fd), FdChannel(send_fd)


class BrokenWorkerProcessError(_abc.BrokenWorkerError):
    __doc__ = f"""{_abc.BrokenWorkerError.__doc__}
    The last argument of the exception is the underlying
    :class:`multiprocessing.Process` which may be inspected for e.g. exit codes.
    """


class WorkerProcCache(_abc.WorkerCache):
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

    def shutdown(self, timeout):
        unclean = []
        killed = []
        for worker in self:
            worker.shutdown()
        deadline = time.perf_counter() + timeout
        for worker in self:
            timeout = deadline - time.perf_counter()
            while timeout > tp_workers.MAX_TIMEOUT:
                worker.proc.join(tp_workers.MAX_TIMEOUT)
                if worker.proc.exitcode is not None:
                    break
                timeout = deadline - time.perf_counter()
            else:
                # guard rare race on macos if exactly == 0.0
                worker.proc.join(timeout or -0.1)
            if worker.proc.exitcode is None:
                worker.kill()
                killed.append(worker.proc)
            elif worker.proc.exitcode:
                unclean.append(worker.proc)
        if unclean or killed:
            for proc in killed:
                proc.join()
            raise BrokenWorkerProcessError(
                f"Graceful shutdown failed: {len(unclean)} nonzero exit codes "
                f"and {len(killed)} forceful terminations.",
                *unclean,
                *killed,
            )


class SpawnProcWorker(_abc.AbstractWorker):
    _proc_counter = count()
    mp_context = multiprocessing.get_context("spawn")

    def __init__(self, idle_timeout, init, retire):
        self._child_recv_pipe, self._send_pipe = self.mp_context.Pipe(duplex=False)
        self._recv_pipe, self._child_send_pipe = self.mp_context.Pipe(duplex=False)
        self._receive_chan, self._send_chan = asyncify_pipes(
            self._recv_pipe.fileno(), self._send_pipe.fileno()
        )
        self.proc = self.mp_context.Process(
            target=tp_workers.worker_behavior,
            args=(
                self._child_recv_pipe,
                self._child_send_pipe,
                idle_timeout,
                dumps(init, protocol=HIGHEST_PROTOCOL),
                dumps(retire, protocol=HIGHEST_PROTOCOL),
            ),
            name=f"trio-parallel worker process {next(self._proc_counter)}",
            daemon=True,
        )

    async def start(self):
        await trio.to_thread.run_sync(self.proc.start)
        # XXX: We must explicitly close these after start to see child closures
        self._child_send_pipe.close()
        self._child_recv_pipe.close()

        # The following is mainly needed in the case of accidental recursive spawn
        async def wait_then_fail():
            await self.wait()
            raise BrokenWorkerProcessError("Worker failed to start", self.proc)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(wait_then_fail)
            try:
                code = await self._receive_chan.receive()
            except BaseException:
                self.kill()
                with trio.CancelScope(shield=True):
                    await self.wait()  # noqa: TRIO102
                raise
            assert code == tp_workers.ACK
            nursery.cancel_scope.cancel()

    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        try:
            job = dumps((sync_fn, args), protocol=HIGHEST_PROTOCOL)
        except BaseException as exc:  # noqa: TRIO103
            return Error(exc)  # noqa: TRIO104, TRIO107

        try:
            try:
                await self._send_chan.send(job)
            except trio.BrokenResourceError:
                with trio.CancelScope(shield=True):
                    await self.wait()
                return None

            try:
                return loads(await self._receive_chan.receive())
            except trio.EndOfChannel:
                self._send_pipe.close()  # edge case: free proc spinning on recv_bytes
                with trio.CancelScope(shield=True):
                    await self.wait()
                raise BrokenWorkerProcessError(
                    "Worker died unexpectedly:", self.proc
                ) from None
        except BaseException:
            # cancellations require kill by contract
            # other exceptions will almost certainly leave us in an
            # unrecoverable state requiring kill as well
            self.kill()
            with trio.CancelScope(shield=True):
                await self.wait()  # noqa: TRIO102
            raise

    def is_alive(self):
        # if the proc is alive, there is a race condition where it could be
        # dying. This call reaps zombie children on Unix.
        return self.proc.is_alive()

    def shutdown(self):
        self._send_pipe.close()

    def kill(self):
        self.proc.kill()

    async def wait(self):
        if self.proc.exitcode is not None:
            await trio.lowlevel.cancel_shielded_checkpoint()
            return self.proc.exitcode
        if self.proc.pid is None:
            await trio.lowlevel.cancel_shielded_checkpoint()
            return None  # waiting before started
        await wait(self.proc.sentinel)
        # fix a macos race: Trio GH#1296
        self.proc.join()
        # unfortunately join does not return exitcode
        return self.proc.exitcode

    def __del__(self):
        # Avoid __del__ errors on cleanup: Trio GH#174, GH#1767
        # multiprocessing will close them for us if initialized
        # but practically they are always initialized, hence pragma
        if hasattr(self, "_send_chan"):  # pragma: no branch
            self._send_chan.detach()
        if hasattr(self, "_receive_chan"):  # pragma: no branch
            self._receive_chan.detach()


WORKER_PROC_MAP = {"spawn": (SpawnProcWorker, WorkerProcCache)}

_all_start_methods = set(multiprocessing.get_all_start_methods())

if "forkserver" in _all_start_methods:  # pragma: no branch

    class ForkserverProcWorker(SpawnProcWorker):
        mp_context = multiprocessing.get_context("forkserver")

    WORKER_PROC_MAP["forkserver"] = ForkserverProcWorker, WorkerProcCache

if "fork" in _all_start_methods:  # pragma: no branch

    class ForkProcWorker(SpawnProcWorker):
        mp_context = multiprocessing.get_context("fork")

        def __init__(self, idle_timeout, init, retire):
            super().__init__(idle_timeout, None, None)
            self._idle_timeout = idle_timeout
            self._init = init
            self._retire = retire
            self.proc.run = self._run

        def _run(self):
            self._send_pipe.close()
            self._recv_pipe.close()
            tp_workers.worker_behavior(
                self._child_recv_pipe,
                self._child_send_pipe,
                self._idle_timeout,
                self._init,
                self._retire,
            )

        async def start(self):
            await trio.lowlevel.checkpoint_if_cancelled()
            # on fork, doing start() in a thread is racy, and should be
            # fast enough to be considered non-blocking anyway
            self.proc.start()
            # XXX: We must explicitly close these after start to see child closures
            self._child_send_pipe.close()
            self._child_recv_pipe.close()
            # These are possibly large and deallocation would be desireable
            del self._init
            del self._retire
            # Breaks a reference cycle
            del self.proc.run

    WORKER_PROC_MAP["fork"] = ForkProcWorker, WorkerProcCache

del _all_start_methods
