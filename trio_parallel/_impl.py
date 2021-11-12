import atexit
import os
from enum import Enum
from itertools import count
from typing import Type, Callable, Any

import attr
from async_generator import asynccontextmanager

from ._proc import WORKER_PROC_MAP
from ._abc import WorkerCache, AbstractWorker, NoPublicConstructor

# Sane default might be to expect cpu-bound work
DEFAULT_LIMIT = os.cpu_count() or 1
limiter_runvar = None
ATEXIT_SHUTDOWN_GRACE_PERIOD = 30.0


def current_default_worker_limiter():
    """Get the default `~trio.CapacityLimiter` used by
    :func:`trio_parallel.run_sync`.

    The most common reason to call this would be if you want to modify its
    :attr:`~trio.CapacityLimiter.total_tokens` attribute. This attribute
    is initialized to the number of CPUs reported by :func:`os.cpu_count`.

    """
    import trio

    global limiter_runvar
    if limiter_runvar is None:
        limiter_runvar = trio.lowlevel.RunVar("trio_parallel")
    try:
        return limiter_runvar.get()
    except LookupError:
        limiter = trio.CapacityLimiter(DEFAULT_LIMIT)
        limiter_runvar.set(limiter)
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


@attr.s(slots=True, eq=False)
class ContextLifetimeManager:
    closed = attr.ib(False)
    task = attr.ib(None)
    # Counters are used for thread safety of the default cache
    enter_counter = attr.ib(factory=lambda: count(1))
    exit_counter = attr.ib(factory=lambda: count(1))

    async def __aenter__(self):
        # only async to save indentation
        if self.closed:
            import trio

            raise trio.ClosedResourceError
        next(self.enter_counter)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # only async to save indentation
        next(self.exit_counter)
        if self.task and self.calc_running() == 0:
            import trio

            trio.lowlevel.reschedule(self.task)

    async def close_and_wait(self):
        assert not self.closed
        self.closed = True
        if self.calc_running() != 0:
            import trio

            self.task = trio.lowlevel.current_task()
            await trio.lowlevel.wait_task_rescheduled(
                # never cancelled anyway
                lambda _: trio.lowlevel.Abort.FAILED  # pragma: no cover
            )
            self.task = None

    def calc_running(self):
        # __reduce__ is the only count API that can extract the internal int value
        # without incrementing it. Let's hope it's stable!!
        return (
            self.enter_counter.__reduce__()[1][0] - self.exit_counter.__reduce__()[1][0]
        )


@attr.s(auto_attribs=True, slots=True, frozen=True)
class WorkerContextStatistics:
    idle_workers: int
    running_workers: int


def check_non_negative(instance, attribute, value):
    if value < 0.0:
        raise ValueError(f"{attribute} must be non-negative, was {value}")


@attr.s(frozen=True, eq=False)
class WorkerContext(metaclass=NoPublicConstructor):
    """A representation of a context where workers have a custom configuration.

    Instances of this class are to be created using :func:`open_worker_context`,
    and cannot be directly instantiated. The arguments to :func:`open_worker_context`
    that created an instance are available for inspection as read-only attributes.

    This class provides a ``statistics()`` method, which returns an object with the
    following fields:

    * ``idle_workers``: The number of live workers currently stored in the context's
      cache.
    * ``running_workers``: The number of workers currently executing jobs.
    """

    idle_timeout: float = attr.ib(
        default=600.0,
        validator=check_non_negative,
    )
    init: Callable[[], Any] = attr.ib(
        default=bool,
        validator=attr.validators.is_callable(),
    )
    retire: Callable[[], Any] = attr.ib(
        default=bool,
        validator=attr.validators.is_callable(),
    )
    grace_period: float = attr.ib(
        default=30.0,
        validator=check_non_negative,
    )
    worker_type: WorkerType = attr.ib(
        default=WorkerType.SPAWN,
        validator=attr.validators.in_(WorkerType),
    )
    _worker_class: Type[AbstractWorker] = attr.ib(repr=False, init=False)
    _worker_cache: WorkerCache = attr.ib(repr=False, init=False)
    _lifetime: ContextLifetimeManager = attr.ib(
        factory=ContextLifetimeManager, repr=False, init=False
    )

    def __attrs_post_init__(self):
        worker_class, worker_cache_class = WORKER_MAP[self.worker_type]
        self.__dict__["_worker_class"] = worker_class
        self.__dict__["_worker_cache"] = worker_cache_class()

    async def run_sync(self, sync_fn, *args, cancellable=False, limiter=None):
        """Run ``sync_fn(*args)`` in a separate process and return/raise it's outcome.

        Uses the customized attributes of the context. See
        :func:`trio_parallel.run_sync()` for details.

        Raises:
            trio.ClosedResourceError: if this method is run on a closed context"""
        import trio

        if limiter is None:
            limiter = current_default_worker_limiter()

        async with limiter, self._lifetime:
            self._worker_cache.prune()
            while True:
                with trio.CancelScope(shield=not cancellable):
                    try:
                        worker = self._worker_cache.pop()
                    except IndexError:
                        worker = self._worker_class(
                            self.idle_timeout, self.init, self.retire
                        )
                        await worker.start()
                    result = await worker.run_sync(sync_fn, *args)

                if result is None:
                    # Prevent uninterruptible loop
                    # when KI-protected & cancellable=False
                    await trio.lowlevel.checkpoint_if_cancelled()
                else:
                    self._worker_cache.append(worker)
                    return result.unwrap()

    async def _aclose(self):
        import trio

        with trio.CancelScope(shield=True):
            await self._lifetime.close_and_wait()
            await trio.to_thread.run_sync(
                self._worker_cache.shutdown, self.grace_period
            )

    def statistics(self):
        self._worker_cache.prune()
        return WorkerContextStatistics(
            idle_workers=len(self._worker_cache),
            running_workers=self._lifetime.calc_running(),
        )


DEFAULT_CONTEXT = WorkerContext._create()  # intentionally skip open_worker_context


def default_context_statistics():
    """Return the statistics corresponding to the default context.

    Because the default context used by `trio_parallel.run_sync` is a private
    implementation detail, this function serves to provide public access to the default
    context statistics object.

    .. note::

       The statistics are only eventually consistent in the case of multiple trio
       threads concurrently using `trio_parallel.run_sync`."""
    return DEFAULT_CONTEXT.statistics()


@asynccontextmanager
async def open_worker_context(
    idle_timeout=DEFAULT_CONTEXT.idle_timeout,
    init=DEFAULT_CONTEXT.init,
    retire=DEFAULT_CONTEXT.retire,
    grace_period=DEFAULT_CONTEXT.grace_period,
    worker_type=WorkerType.SPAWN,
):
    """Create a new, customized worker context with isolated workers.

    By default, :func:`trio_parallel.run_sync` draws workers from a global cache
    that is shared across sequential and between concurrent :func:`trio.run()`
    calls, with workers' lifetimes limited to the life of the main process. This
    covers most use cases, but for the many edge cases, this async context manager
    yields a `WorkerContext` object on which `~WorkerContext.run_sync()` pulls workers
    from an isolated cache with behavior specified by the class arguments. It is only
    advised to use this if specific control over worker type, state, or
    lifetime is required.

    The context will automatically wait for any running workers to become idle when
    exiting the scope. Since this wait cannot be cancelled, it is more convenient to
    only pass the context object to tasks that cannot outlive the scope, for example,
    by using a :class:`~trio.Nursery`.

    Args:
      idle_timeout (float): The time in seconds an idle worker will
          wait for a CPU-bound job before shutting down and releasing its own
          resources. Pass `math.inf` to wait forever. MUST be non-negative.
      init (Callable[[], bool]):
          An object to call within the worker before waiting for jobs.
          This is suitable for initializing worker state so that such stateful logic
          does not need to be included in functions passed to
          :func:`WorkerContext.run_sync`. MUST be callable without arguments.
      retire (Callable[[], bool]):
          An object to call within the worker after executing a CPU-bound job.
          The return value indicates whether worker should be retired (shut down.)
          By default, workers are never retired.
          The process-global environment is stable between calls. Among other things,
          that means that storing state in global variables works.
          MUST be callable without arguments.
      grace_period (float): The time in seconds to wait in when closing for workers to
          exit before issuing SIGKILL/TerminateProcess and raising `BrokenWorkerError`.
          Pass `math.inf` to wait forever. MUST be non-negative.
      worker_type (WorkerType): The kind of worker to create, see :class:`WorkerType`.

    Raises:
      ValueError | TypeError: if an invalid value is passed for an argument, such as a
          negative timeout.
      BrokenWorkerError: if a worker does not shut down cleanly when exiting the scope.

    .. note::

       The callables passed to retire MUST not raise! Doing so will result in a
       :class:`BrokenWorkerError` at an indeterminate future
       :func:`WorkerContext.run_sync` call.

    """
    ctx = WorkerContext._create(idle_timeout, init, retire, grace_period, worker_type)
    try:
        yield ctx
    finally:
        await ctx._aclose()


def atexit_shutdown_grace_period(grace_period=-1.0):
    """Return and optionally set the default worker cache shutdown grace period.

    This only affects the `atexit` behavior of the default context corresponding to
    :func:`trio_parallel.run_sync`. Existing and future `WorkerContext` instances
    are unaffected.

    Args:
      grace_period (float): The time in seconds to wait for workers to
          exit before issuing SIGKILL/TerminateProcess and raising `BrokenWorkerError`.
          Pass `math.inf` to wait forever. Pass a negative value or use the default
          value to return the current value without modifying it.

    Returns:
      float: The current grace period in seconds.

    .. note::

       This function is subject to threading race conditions."""

    global ATEXIT_SHUTDOWN_GRACE_PERIOD

    if grace_period >= 0.0:
        ATEXIT_SHUTDOWN_GRACE_PERIOD = grace_period
    return ATEXIT_SHUTDOWN_GRACE_PERIOD


@atexit.register
def graceful_default_shutdown():
    # need to late-bind the context attribute lookup so
    # don't use atexit.register(fn,*args) form
    DEFAULT_CONTEXT._worker_cache.shutdown(ATEXIT_SHUTDOWN_GRACE_PERIOD)


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
    To customize worker behavior, use :func:`open_worker_context`.

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
    return await DEFAULT_CONTEXT.run_sync(
        sync_fn, *args, cancellable=cancellable, limiter=limiter
    )
