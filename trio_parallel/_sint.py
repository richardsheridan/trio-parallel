import _interpreters as interpreters
import _interpchannels as channels
from typing import Callable, Optional
import trio

from outcome import Outcome, Error
from . import _abc
from inspect import iscoroutine
from pickle import HIGHEST_PROTOCOL
from time import perf_counter, sleep

try:
    from cloudpickle import dumps, loads
except ImportError:
    from pickle import dumps, loads

from outcome import Error
from tblib.pickling_support import install as install_pickling_support

import _trio_parallel_workers as tp_workers


class BrokenWorkerSubinterpreterError(_abc.BrokenWorkerError):
    __doc__ = f"""{_abc.BrokenWorkerError.__doc__}
    The last argument of the exception is the underlying
    ``_xxsubinterpreters`` ``id`` which may be inspected.
    """


def handle_job(job):
    try:
        fn, args = loads(job)
        ret = fn(*args)
        if iscoroutine(ret):
            # Manually close coroutine to avoid RuntimeWarnings
            ret.close()
            raise TypeError(
                "trio-parallel worker expected a sync function, but {!r} appears "
                "to be asynchronous".format(getattr(fn, "__qualname__", fn))
            )
        return ret
    except BaseException as e:
        install_pickling_support(e)
        raise e


def safe_dumps(result):
    try:
        return dumps(result, protocol=HIGHEST_PROTOCOL)
    except BaseException as exc:  # noqa: TRIO103
        return dumps(Error(exc), protocol=HIGHEST_PROTOCOL)  # noqa: TRIO104


class WorkerSintCache(_abc.WorkerCache):
    def prune(self):
        # remove sints that have died from the idle timeout
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
        for worker in self:
            worker.shutdown()
        deadline = perf_counter() + timeout
        for worker in self:
            while worker.is_alive():
                if perf_counter() > deadline:
                    unclean.append(worker.interp)
                    break
                sleep(0.1)
        if unclean:
            raise BrokenWorkerSubinterpreterError(
                f"Graceful shutdown failed: "
                f"{len(unclean)} remaining workers running.",
                *unclean,
            )


class SintWorker(_abc.AbstractWorker):
    def __init__(
        self,
        idle_timeout: float,
        init: Optional[Callable[[], bool]],
        retire: Optional[Callable[[], bool]],
    ):
        self._killed = False
        self._idle_timeout = idle_timeout
        self._init = dumps(init)
        self._retire = dumps(retire)

    async def start(self):
        # Even if this method is overall synchronous, some tests assume it can
        # be cancelled without leaving any worker state behind
        await trio.lowlevel.checkpoint_if_cancelled()
        send_cid = self._send_cid = channels.create(2)
        recv_cid = self._recv_cid = channels.create(2)
        interp = self.interp = interpreters.create()
        script = f"""from math import inf
        \nfrom _trio_parallel_workers import sint_worker_behavior as x
        \nx({self._idle_timeout},{int(send_cid)},{int(recv_cid)})
        """
        del self._idle_timeout

        def sint_thread_behavior():
            interpreters.run_string(interp, script)

        def sint_thread_deliver(result):
            result.unwrap()

        trio.lowlevel.start_thread_soon(sint_thread_behavior, sint_thread_deliver)
        channels.send(send_cid, self._init, blocking=False)
        channels.send(send_cid, self._retire, blocking=False)
        code = None
        while code is None:
            (code, _) = channels.recv(recv_cid, None)
            await trio.sleep(0.1)
        assert code == tp_workers.ACK, code
        # worker has used a ref for both channels by now, release unneeded ends
        # channels.release(send_cid, recv=True)
        # channels.release(recv_cid, send=True)

    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        try:
            job = dumps((sync_fn, args), protocol=HIGHEST_PROTOCOL)
        except BaseException as exc:  # noqa: TRIO103
            return Error(exc)  # noqa: TRIO104, TRIO910

        try:
            try:
                channels.send(self._send_cid, job, blocking=False)
            except channels.ChannelClosedError:
                with trio.CancelScope(shield=True):
                    await self.wait()
                return None

            try:
                while (result := channels.recv(self._recv_cid, False)[0]) is False:
                    await trio.sleep(0.10)
                return loads(result)
            except channels.ChannelClosedError:
                with trio.CancelScope(shield=True):
                    await self.wait()
                raise BrokenWorkerSubinterpreterError(
                    "Worker died unexpectedly:", self.interp
                ) from None
        except BaseException:
            # cancellations require kill by contract
            # other exceptions will almost certainly leave us in an
            # unrecoverable state requiring kill as well
            self.kill()
            with trio.CancelScope(shield=True):
                await self.wait()  # noqa: TRIO102
            raise

    def shutdown(self):
        try:
            channels.close(self._send_cid)
        except AttributeError:
            pass

    def is_alive(self):
        if not hasattr(self, "interp"):
            return False
        return interpreters.is_running(self.interp)

    def kill(self):
        # this probably won't work for some time:
        # https://github.com/python/cpython/blob/ee40b3e20d9b8d62a9b36b777dff42db1e9049d5/Modules/_xxsubinterpretersmodule.c#L575-L580
        # interpreters.destroy(self.interp)
        channels.close(self._send_cid)
        channels.close(self._recv_cid)
        self._killed = True

    async def wait(self):
        if not hasattr(self, "interp"):
            return None
        while self.is_alive():
            await trio.sleep(0.1)
        return -15 if self._killed else 0

    def __del__(self):
        # No leaks allowed, regardless of the consequences
        try:
            channels.destroy(self._send_cid)
            channels.destroy(self._recv_cid)
            interpreters.destroy(self.interp)
        except AttributeError:
            pass


WORKER_SINT_MAP = {"sint": (SintWorker, WorkerSintCache)}
