import os
import contextvars
from collections import deque
from multiprocessing import get_context
from multiprocessing.context import BaseContext

import trio
from contextlib import contextmanager

from ._proc import WorkerProc


# Sane default might be to expect cpu-bound work
DEFAULT_LIMIT = os.cpu_count() or 1
_limiter_local = trio.lowlevel.RunVar("proc_limiter")


def current_default_worker_limiter():
    """Get the default `~trio.CapacityLimiter` used by
    `trio_parallel.run_sync`.

    The most common reason to call this would be if you want to modify its
    :attr:`~trio.CapacityLimiter.total_tokens` attribute. This attribute
    is initialized to the number of CPUs reported by :func:`os.cpu_count`.

    """
    try:
        return _limiter_local.get()
    except LookupError:
        limiter = trio.CapacityLimiter(DEFAULT_LIMIT)
        _limiter_local.set(limiter)
        return limiter


class WorkerCache(deque):
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

    def clear(self):
        try:
            while True:
                proc = self.pop()
                proc.kill()
                trio.lowlevel.spawn_system_task(proc.wait)
        except IndexError:
            pass


DEFAULT_CACHE = WorkerCache()
DEFAULT_MP_CONTEXT = get_context("spawn")
DEFAULT_IDLE_TIMEOUT = 600
DEFAULT_MAX_JOBS = float("inf")
_worker_context = contextvars.ContextVar(
    "worker_context",
    default=(DEFAULT_CACHE, DEFAULT_MP_CONTEXT, DEFAULT_IDLE_TIMEOUT, DEFAULT_MAX_JOBS),
)


@contextmanager
def cache_scope(
    mp_context=DEFAULT_MP_CONTEXT,
    idle_timeout=DEFAULT_IDLE_TIMEOUT,
    max_jobs=DEFAULT_MAX_JOBS,
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
        worker_cache.clear()


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
        result = None
        while result is None:
            # Prevent uninterruptible loop when KI & cancellable=False
            await trio.lowlevel.checkpoint_if_cancelled()

            try:
                proc = worker_cache.pop()
            except IndexError:
                proc = WorkerProc(mp_context, idle_timeout, max_jobs)

            with trio.CancelScope(shield=not cancellable):
                result = await proc.run_sync(sync_fn, *args)

    worker_cache.append(proc)
    return result.unwrap()
