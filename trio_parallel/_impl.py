import os
import contextvars
from collections import deque
from multiprocessing import get_context
from multiprocessing.context import BaseContext

import trio
from async_generator import asynccontextmanager

from ._proc import WorkerProc
from ._util import BrokenWorkerError


# Sane default might be to expect cpu-bound work
DEFAULT_LIMIT = os.cpu_count() or 1
_limiter_local = trio.lowlevel.RunVar(
    "proc_limiter", default=trio.CapacityLimiter(DEFAULT_LIMIT)
)


def current_default_worker_limiter():
    """Get the default `~trio.CapacityLimiter` used by
    `trio_parallel.run_sync`.

    The most common reason to call this would be if you want to modify its
    :attr:`~trio.CapacityLimiter.total_tokens` attribute. This attribute
    is initialized to the number of CPUs reported by :func:`os.cpu_count`.

    """
    return _limiter_local.get()


class WorkerCache:
    def __init__(self):
        # The cache is a deque rather than dict here since processes can't remove
        # themselves anyways, so we don't need O(1) lookups
        self._cache = deque()
        # NOTE: avoid thread races between Trio runs by only interacting with
        # self._cache via thread-atomic actions like append, pop, del

    def prune(self):
        # take advantage of the oldest proc being on the left to
        # keep iteration O(dead workers)
        try:
            while True:
                proc = self._cache.popleft()
                if proc.is_alive():
                    self._cache.appendleft(proc)
                    return
        except IndexError:
            # Thread safety: it's necessary to end the iteration using this error
            # when the cache is empty, as opposed to `while self._cache`.
            pass

    def push(self, proc):
        self._cache.append(proc)

    def pop(self):
        # Get live, WOKEN worker process or raise IndexError
        while True:
            proc = self._cache.pop()
            try:
                proc.wake_up(0)
            except BrokenWorkerError:
                # proc must have died in the cache, just try again
                continue
            else:
                return proc

    async def clear(self):
        async with trio.open_nursery() as nursery:
            try:
                while True:
                    proc = self._cache.pop()
                    proc.kill()
                    nursery.start_soon(proc.wait)
            except IndexError:
                pass

    def __len__(self):
        return len(self._cache)


DEFAULT_CACHE = WorkerCache()
DEFAULT_MP_CONTEXT = get_context("spawn")
DEFAULT_IDLE_TIMEOUT = 600
DEFAULT_MAX_JOBS = float("inf")
_worker_context = contextvars.ContextVar(
    "worker_context",
    default=(DEFAULT_CACHE, DEFAULT_MP_CONTEXT, DEFAULT_IDLE_TIMEOUT, DEFAULT_MAX_JOBS),
)


@asynccontextmanager
async def cache_scope(
    mp_context=get_context("spawn"), idle_timeout=10, max_jobs=float("inf")
):
    if not isinstance(mp_context, BaseContext):
        # noinspection PyTypeChecker
        mp_context = get_context(mp_context)
    worker_cache = WorkerCache()
    token = _worker_context.set((worker_cache, mp_context, idle_timeout, max_jobs))
    try:
        yield
    finally:
        _worker_context.reset(token)
        with trio.CancelScope(shield=True):
            await worker_cache.clear()


async def run_sync(sync_fn, *args, cancellable=False, limiter=None):
    """Run sync_fn in a separate process

    This is a wrapping of :class:`multiprocessing.Process` that follows the API of
    :func:`trio.to_thread.run_sync`. The intended use of this function is limited:

    - Circumvent the GIL to run CPU-bound functions in parallel
    - Make blocking APIs or infinite loops truly cancellable through
      SIGKILL/TerminateProcess without leaking resources
    - Protect the main process from untrusted/unstable code without leaks

    Other :mod:`multiprocessing` features may work but are not officially
    supported, and all the normal :mod:`multiprocessing` caveats apply. The
    underlying worker processes are cached LIFO and reused to minimize latency.
    Global state cannot be considered stable between and across calls.

    Args:
      sync_fn: An importable or pickleable synchronous callable. See the
          :mod:`multiprocessing` documentation for detailed explanation of
          limitations.
      *args: Positional arguments to pass to sync_fn. If you need keyword
          arguments, use :func:`functools.partial`.
      cancellable (bool): Whether to allow cancellation of this operation.
          Cancellation always involves abrupt termination of the worker process
          with SIGKILL/TerminateProcess. To obtain correct semantics with CTRL+C,
          SIGINT is ignored when raised in workers.
      limiter (None, or trio.CapacityLimiter):
          An object used to limit the number of simultaneous processes. Most
          commonly this will be a `~trio.CapacityLimiter`, but any async
          context manager will succeed.

    Returns:
      Any: Whatever ``sync_fn(*args)`` returns.

    Raises:
      BaseException: Whatever ``sync_fn(*args)`` raises.
      BrokenWorkerError: Indicates the worker died unexpectedly. Not encountered
        in normal use.

    """
    if limiter is None:
        limiter = current_default_worker_limiter()

    worker_cache, mp_context, idle_timeout, max_jobs = _worker_context.get()

    async with limiter:
        worker_cache.prune()

        try:
            proc = worker_cache.pop()
        except IndexError:
            proc = WorkerProc(mp_context, idle_timeout, max_jobs)
            await trio.to_thread.run_sync(proc.wake_up)

        try:
            with trio.CancelScope(shield=not cancellable):
                return await proc.run_sync(sync_fn, *args)
        finally:
            if proc.is_alive():
                worker_cache.push(proc)
