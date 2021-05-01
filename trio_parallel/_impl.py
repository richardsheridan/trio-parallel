import os
import contextvars
from multiprocessing import get_context
from multiprocessing.context import BaseContext

import attr
import trio
from contextlib import contextmanager

from ._proc import WorkerProc, WorkerProcCache
from ._abc import WorkerCache


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


@attr.s(auto_attribs=True, slots=True)
class WorkerContext:
    mp_context: BaseContext = get_context("spawn")
    idle_timeout: float = 600.0
    max_jobs: float = float("inf")
    worker_cache: WorkerCache = attr.ib(factory=WorkerProcCache)

    def new_worker(self):
        return WorkerProc(self.mp_context, self.idle_timeout, self.max_jobs)


DEFAULT_CONTEXT = WorkerContext()  # Mutable and monkeypatch-able!
_worker_context_var = contextvars.ContextVar("worker_context", default=DEFAULT_CONTEXT)


@contextmanager
def cache_scope(
    mp_context=DEFAULT_CONTEXT.mp_context,
    idle_timeout=DEFAULT_CONTEXT.idle_timeout,
    max_jobs=DEFAULT_CONTEXT.max_jobs,
):
    """Create a new, customized worker cache with a scoped lifetime.

    Args:
      mp_context (str): The :mod:`multiprocessing` context to create workers with.
      idle_timeout (float): The time in seconds an idle worker will wait before
          shutting down and releasing its own resources. Must be non-negative.
      max_jobs (float):
          The maximum number of jobs a worker will accept before shutting down
          and releasing its own resources. Must be positive.

    Raises:
      RuntimeError: if you attempt to open a scope outside an async context,
          that is, outside of a :func:`trio.run`.
      ValueError: if an invalid value is passed for an argument, such as a negative
          timeout or a job limit less than one.

    """
    trio.lowlevel.current_task()  # assert early we are in an async context
    if not isinstance(mp_context, BaseContext):
        # noinspection PyTypeChecker
        mp_context = get_context(mp_context)
    if idle_timeout < 0:
        raise ValueError("idle_timeout must be non-negative")
    if max_jobs <= 0:
        raise ValueError("max_jobs must be positive")
    worker_context = WorkerContext(mp_context, idle_timeout, max_jobs)
    token = _worker_context_var.set(worker_context)
    try:
        yield
    finally:
        _worker_context_var.reset(token)
        worker_context.worker_cache.clear()


async def run_sync(sync_fn, *args, cancellable=False, limiter=None):
    """Run sync_fn in a separate process

    The intended use of this function is limited:

    - Circumvent the GIL to run CPU-bound functions in parallel
    - Make blocking APIs or infinite loops truly cancellable through
      SIGKILL/TerminateProcess without leaking resources
    - Protect the main process from untrusted/unstable code without leaks

    By default, this is a wrapping of :class:`multiprocessing.Process` that
    follows the API of :func:`trio.to_thread.run_sync`.
    Other :mod:`multiprocessing` features may work but are not officially
    supported, and all the normal :mod:`multiprocessing` caveats apply.
    To customize this, use the :func:`cache_scope` context manager.

    The underlying workers are cached LIFO and reused to minimize latency.
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

    worker_context = _worker_context_var.get()

    async with limiter:
        worker_context.worker_cache.prune()
        result = None
        while result is None:
            # Prevent uninterruptible loop when KI & cancellable=False
            await trio.lowlevel.checkpoint_if_cancelled()

            try:
                proc = worker_context.worker_cache.pop()
            except IndexError:
                proc = worker_context.new_worker()

            with trio.CancelScope(shield=not cancellable):
                result = await proc.run_sync(sync_fn, *args)

    worker_context.worker_cache.append(proc)
    return result.unwrap()
