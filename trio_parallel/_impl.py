import os
import contextvars
from enum import Enum
from typing import Type, Callable

import attr
import trio
from async_generator import asynccontextmanager

from ._proc import WORKER_PROC_MAP
from ._abc import WorkerCache, AbstractWorker

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


WORKER_MAP = {**WORKER_PROC_MAP}

WorkerType = Enum(
    "WorkerType", ((x.upper(), x) for x in WORKER_MAP), type=str, module=__name__
)
WorkerType.__doc__ = """An Enum of available kinds of workers.

Currently, these correspond to the values of
:func:`multiprocessing.get_all_start_methods`."""


@attr.s(auto_attribs=True, slots=True)
class WorkerContext:
    idle_timeout: float = 600.0
    retire: Callable[[], bool] = bool  # always falsey singleton
    worker_class: Type[AbstractWorker] = WORKER_MAP[WorkerType.SPAWN][0]
    worker_cache: WorkerCache = attr.ib(factory=WORKER_MAP[WorkerType.SPAWN][1])


DEFAULT_CONTEXT = WorkerContext()  # Mutable and monkeypatch-able!
_worker_context_var = contextvars.ContextVar("worker_context", default=DEFAULT_CONTEXT)


@asynccontextmanager
async def cache_scope(
    idle_timeout=DEFAULT_CONTEXT.idle_timeout,
    retire=DEFAULT_CONTEXT.retire,
    worker_type=WorkerType.SPAWN,
):
    """Create a new, customized worker cache with a scoped lifetime.

    Args:
      idle_timeout (float): The time in seconds an idle worker will wait before
          shutting down and releasing its own resources. Must be non-negative.
      retire (Callable[[], bool]):
          An object to call within the worker BEFORE running each CPU-bound job
          that returns a value indicating whether worker should be retired
          BEFORE accepting that job. By default, workers are never retired.
          Note that this function MUST return a falsey value on the first call
          otherwise :func:`run_sync` will get caught in an infinite loop trying
          to find a valid worker.
      worker_type (WorkerType): The kind of worker to create, see :class:`WorkerType`.

    Raises:
      RuntimeError: if you attempt to open a scope outside an async context,
          that is, outside of a :func:`trio.run`.
      ValueError: if an invalid value is passed for an argument, such as a negative
          timeout.

    """
    if not isinstance(worker_type, WorkerType):
        raise ValueError("worker_type must be a member of WorkerType")
    elif idle_timeout < 0:
        raise ValueError("idle_timeout must be non-negative")
    elif not callable(retire):
        raise ValueError("retire must be callable (with no arguments)")
    # TODO: better ergonomics for truthy first call to retire()
    worker_class, worker_cache = WORKER_MAP[worker_type]
    worker_context = WorkerContext(idle_timeout, retire, worker_class, worker_cache())
    token = _worker_context_var.set(worker_context)
    try:
        yield
    finally:
        _worker_context_var.reset(token)
        await worker_context.worker_cache.clear()


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

    ctx = _worker_context_var.get()

    async with limiter:
        ctx.worker_cache.prune()
        result = None
        while result is None:
            # Prevent uninterruptible loop when KI & cancellable=False
            await trio.lowlevel.checkpoint_if_cancelled()

            try:
                proc = ctx.worker_cache.pop()
            except IndexError:
                proc = ctx.worker_class(ctx.idle_timeout, ctx.retire)

            with trio.CancelScope(shield=not cancellable):
                result = await proc.run_sync(sync_fn, *args)

    ctx.worker_cache.append(proc)
    return result.unwrap()
