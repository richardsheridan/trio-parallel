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
    from trio.lowlevel import WaitForSingleObject as lowlevel_wait
    from ._windows_pipes import (
        PipeReceiveChannel as RecvChan,
        PipeSendChannel as SendChan,
    )


else:
    from trio.lowlevel import wait_readable as lowlevel_wait
    from ._posix_pipes import FdChannel as RecvChan

    SendChan = RecvChan


class BrokenWorkerProcessError(_abc.BrokenWorkerError):
    __doc__ = f"""{_abc.BrokenWorkerError.__doc__}
    The last argument of the exception is the underlying
    :class:`multiprocessing.Process` which may be inspected for e.g. exit codes.
    """


class SpawnProcWorker(_abc.AbstractWorker):
    _proc_counter = count()
    mp_context = multiprocessing.get_context("spawn")

    def __init__(self, idle_timeout, init, retire):
        self._child_recv_pipe, self._send_pipe = self.mp_context.Pipe(duplex=False)
        self._recv_pipe, self._child_send_pipe = self.mp_context.Pipe(duplex=False)
        self._receive_chan = RecvChan(self._recv_pipe.fileno())
        self._send_chan = SendChan(self._send_pipe.fileno())
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

        if sys.platform != "win32":
            return

        # Give a nice error on accidental recursive spawn instead of hanging
        async def wait_for_ack():
            try:
                code = await self._receive_chan.receive()
                assert code == tp_workers.ACK
            except BaseException:
                self.kill()
                with trio.CancelScope(shield=True):
                    await self.wait()  # noqa: ASYNC102
                raise
            nursery.cancel_scope.cancel()

        exitcode = None
        async with trio.open_nursery() as nursery:
            nursery.start_soon(wait_for_ack)
            exitcode = await self.wait()
            nursery.cancel_scope.cancel()
        if exitcode is not None:
            raise BrokenWorkerProcessError("Worker failed to start", self.proc)

    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        try:
            job = dumps((sync_fn, args), protocol=HIGHEST_PROTOCOL)
        except BaseException as exc:  # noqa: ASYNC103
            return Error(exc)  # noqa: ASYNC104, ASYNC910

        try:
            try:
                await self._send_chan.send(job)
            except trio.BrokenResourceError:
                with trio.CancelScope(shield=True):
                    await self.wait()
                return None

            try:
                result = loads(await self._receive_chan.receive())
            except trio.EndOfChannel:
                self._send_pipe.close()  # edge case: free proc spinning on recv_bytes
                with trio.CancelScope(shield=True):
                    await self.wait()  # noqa: ASYNC120
                raise BrokenWorkerProcessError(
                    "Worker died unexpectedly:", self.proc
                ) from None
        except BaseException:
            # cancellations require kill by contract
            # other exceptions will almost certainly leave us in an
            # unrecoverable state requiring kill as well
            self.kill()
            with trio.CancelScope(shield=True):
                await self.wait()  # noqa: ASYNC102
            raise

        if result is None:
            # race in worker_behavior cleanup was triggered
            with trio.CancelScope(shield=True):
                await self.wait()  # noqa: ASYNC102

        return result

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
        await lowlevel_wait(self.proc.sentinel)
        # fix a macos race: Trio GH#1296
        self.proc.join()
        # unfortunately join does not return exitcode
        return self.proc.exitcode


class WorkerProcCache(_abc.WorkerCache[SpawnProcWorker]):
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
