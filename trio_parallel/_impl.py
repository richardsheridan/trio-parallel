import atexit
import os
import contextvars
from enum import Enum
from typing import Type, Callable, Optional

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
:func:`multiprocessing.get_all_start_methods`, which vary by platform.
``WorkerType.SPAWN`` is the default and is supported on all platforms.
``WorkerType.FORKSERVER`` is available on POSIX platforms and could be an
optimization if workers need to be killed/restarted often.
``WorkerType.FORK`` is available on POSIX for experimentation, but not recommended."""


@attr.s(auto_attribs=True, slots=True, eq=False)
class WorkerContext:
    idle_timeout: Optional[float] = 600.0
    init: Callable[[], bool] = bool
    retire: Callable[[], bool] = bool  # always falsey singleton
    worker_class: Type[AbstractWorker] = WORKER_MAP[WorkerType.SPAWN][0]
    worker_cache: WorkerCache = attr.ib(factory=WORKER_MAP[WorkerType.SPAWN][1])


DEFAULT_CONTEXT = WorkerContext()  # Mutable and monkeypatch-able!
_worker_context_var = contextvars.ContextVar("worker_context", default=DEFAULT_CONTEXT)
DEFAULT_SHUTDOWN_GRACE_PERIOD = 30.0


def default_shutdown_grace_period(grace_period=-1.0):
    """Return and optionally set the default worker cache shutdown grace period.

    Args:
      grace_period (Optional[float]): The time in seconds to wait for workers to
          exit before issuing SIGKILL/TerminateProcess and raising `BrokenWorkerError`.
          Pass `None` to wait forever, as `math.inf` will fail. Pass a negative value
          to return the current value without modifying it.

    Returns:
      Optional[float]: The current grace period in seconds or `None`.

    .. note::

       This function is subject to threading race conditions."""

    global DEFAULT_SHUTDOWN_GRACE_PERIOD

    if grace_period is None:
        DEFAULT_SHUTDOWN_GRACE_PERIOD = None
    elif grace_period >= 0.0:
        DEFAULT_SHUTDOWN_GRACE_PERIOD = grace_period
    return DEFAULT_SHUTDOWN_GRACE_PERIOD


@atexit.register
def graceful_default_shutdown():
    # need to late-bind the context attribute lookup
    DEFAULT_CONTEXT.worker_cache.shutdown(DEFAULT_SHUTDOWN_GRACE_PERIOD)


@asynccontextmanager
async def cache_scope(
    idle_timeout=DEFAULT_CONTEXT.idle_timeout,
    init=DEFAULT_CONTEXT.retire,
    retire=DEFAULT_CONTEXT.retire,
    grace_period=DEFAULT_SHUTDOWN_GRACE_PERIOD,
    worker_type=WorkerType.SPAWN,
):
    """Create a new, customized worker cache with workers confined to
    a scoped lifetime.

    By default, :func:`trio_parallel.run_sync` draws workers from a global cache
    that is shared across sequential and between concurrent :func:`trio.run()`
    calls, with workers' lifetimes limited to the life of the main process. This
    covers most use cases, but for the many edge cases, this context manager
    defines a scope wherein each call to :func:`run_sync` pulls workers from an
    isolated cache with behavior specified by the arguments. It is only advised
    to use this context manager if specific control over worker type, state, or
    lifetime is required.

    Args:
      idle_timeout (Optional[float]): The time in seconds an idle worker will
          wait for a CPU-bound job before shutting down and releasing its own
          resources. Pass `None` to wait forever, as `math.inf` will fail.
          MUST be non-negative if not `None`.
      init (Callable[[], bool]):
          An object to call within the worker before waiting for jobs.
          This is suitable for initializing worker state so that such stateful logic
          does not need to be included in functions passed to :func:`run_sync`.
          MUST be callable without arguments.
      retire (Callable[[], bool]):
          An object to call within the worker after executing a CPU-bound job.
          The return value indicates whether worker should be retired (shut down.)
          By default, workers are never retired.
          The process-global environment is stable between calls. Among other things,
          that means that storing state in global variables works.
          MUST be callable without arguments.
      grace_period (Optional[float]): The time in seconds to wait for workers to
          exit before issuing SIGKILL/TerminateProcess and raising `BrokenWorkerError`.
          Pass `None` to wait forever, as `math.inf` will fail.
          MUST be non-negative if not `None`.
      worker_type (WorkerType): The kind of worker to create, see :class:`WorkerType`.

    Raises:
      ValueError: if an invalid value is passed for an argument, such as a negative
          timeout.
      BrokenWorkerError: if a worker does not shut down cleanly when exiting the scope.

    .. note::

       The callables passed to retire MUST not raise! Doing so will result in a
       :class:`BrokenWorkerError` at an indeterminate future :func:`run_sync` call.
    """
    if not isinstance(worker_type, WorkerType):
        raise TypeError("worker_type must be a member of WorkerType")
    elif idle_timeout is not None and idle_timeout < 0.0:
        raise ValueError("idle_timeout must be non-negative or None")
    elif not callable(init):
        raise TypeError("init must be callable (with no arguments)")
    elif not callable(retire):
        raise TypeError("retire must be callable (with no arguments)")
    elif grace_period is not None and grace_period < 0.0:
        raise ValueError("grace_period must be non-negative or None")
    worker_class, worker_cache = WORKER_MAP[worker_type]
    worker_context = WorkerContext(
        idle_timeout, init, retire, worker_class, worker_cache()
    )
    token = _worker_context_var.set(worker_context)
    try:
        yield
    finally:
        _worker_context_var.reset(token)
        with trio.CancelScope(shield=True):
            await trio.to_thread.run_sync(
                worker_context.worker_cache.shutdown, grace_period
            )


async def run_sync(sync_fn, *args, cancellable=False, limiter=None):
    """Run ``sync_fn(*args)`` in a separate process and return/raise it's outcome.

    This function is intended to enable the following:

    - Circumventing the GIL to run CPU-bound functions in parallel
    - Making blocking APIs or infinite loops truly cancellable through
      SIGKILL/TerminateProcess without leaking resources
    - Protecting the main process from unstable/crashy code

    Currently, this is a wrapping of :class:`multiprocessing.Process` that
    follows the API of :func:`trio.to_thread.run_sync`.
    Other :mod:`multiprocessing` features may work but are not officially
    supported, and all the normal :mod:`multiprocessing` caveats apply.
    To customize worker behavior, use :func:`trio_parallel.cache_scope`.

    The underlying workers are cached LIFO and reused to minimize latency.
    Global state of the workers is not stable between and across calls.

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
        while True:
            try:
                worker = ctx.worker_cache.pop()
            except IndexError:
                worker = ctx.worker_class(ctx.idle_timeout, ctx.init, ctx.retire)

            with trio.CancelScope(shield=not cancellable):
                result = await worker.run_sync(sync_fn, *args)

            if result is None:
                # Prevent uninterruptible loop when KI-protected & cancellable=False
                await trio.lowlevel.checkpoint_if_cancelled()
            else:
                ctx.worker_cache.append(worker)
                return result.unwrap()
