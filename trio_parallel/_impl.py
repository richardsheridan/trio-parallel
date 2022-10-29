import atexit
import os
import sys
import threading
import warnings
from contextlib import asynccontextmanager
from enum import Enum
from itertools import count
from typing import Type, Callable, Any, TypeVar

import attr
import trio

from ._proc import WORKER_PROC_MAP
from ._abc import WorkerCache, AbstractWorker, NoPublicConstructor

T = TypeVar("T")

# Sane default might be to expect cpu-bound work
DEFAULT_LIMIT = os.cpu_count() or 1
limiter_runvar = trio.lowlevel.RunVar("trio_parallel")


def current_default_worker_limiter():
    """Get the default `~trio.CapacityLimiter` used by
    :func:`trio_parallel.run_sync`.

    The most common reason to call this would be if you want to modify its
    :attr:`~trio.CapacityLimiter.total_tokens` attribute. This attribute
    is initialized to the number of CPUs reported by :func:`os.cpu_count`.

    """
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

Instances of this Enum can be passed to :func:`open_worker_context` or
:func:`configure_default_context` to customize worker startup behavior.

Currently, these correspond to the values of
:func:`multiprocessing.get_all_start_methods`, which vary by platform.
``WorkerType.SPAWN`` is the default and is supported on all platforms.
``WorkerType.FORKSERVER`` is available on POSIX platforms and could be an
optimization if workers need to be killed/restarted often.
``WorkerType.FORK`` is available on POSIX for experimentation, but not
recommended."""


@attr.s(slots=True, eq=False)
class ContextLifetimeManager:
    waiting_task = attr.ib(None)
    # Counters are used for thread safety of the default cache
    enter_counter = attr.ib(factory=lambda: count(1))
    exit_counter = attr.ib(factory=lambda: count(1))

    async def __aenter__(self):  # noqa: TRIO107
        # only async to save indentation
        if self.waiting_task:
            raise trio.ClosedResourceError
        next(self.enter_counter)

    async def __aexit__(self, exc_type, exc_val, exc_tb):  # noqa: TRIO107
        # only async to save indentation
        next(self.exit_counter)
        if self.waiting_task:
            if self.calc_running() == 0:
                trio.lowlevel.reschedule(self.waiting_task)

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
    """A reification of a context where workers have a custom configuration.

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
        worker_class, cache_class = WORKER_MAP[self.worker_type]
        self.__dict__["_worker_class"] = worker_class
        self.__dict__["_worker_cache"] = cache_class()

    @trio.lowlevel.enable_ki_protection
    async def run_sync(
        self,
        sync_fn: Callable[..., T],
        *args,
        cancellable: bool = False,
        limiter: trio.CapacityLimiter = None,
    ) -> T:
        """Run ``sync_fn(*args)`` in a separate process and return/raise its outcome.

        Behaves according to the customized attributes of the context. See
        :func:`trio_parallel.run_sync()` for details.

        Raises:
            trio.ClosedResourceError: if this method is run on a closed context"""
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

    async def _aclose(self, grace_period=None):
        if grace_period is None:
            grace_period = self.grace_period
        assert not self._lifetime.waiting_task
        self._lifetime.waiting_task = trio.lowlevel.current_task()
        with trio.CancelScope(shield=True):
            if self._lifetime.calc_running() != 0:
                await trio.sleep_forever()  # woken by self._lifetime.__aexit__
            await trio.to_thread.run_sync(self._worker_cache.shutdown, grace_period)

    @trio.lowlevel.enable_ki_protection
    def statistics(self):
        self._worker_cache.prune()
        return WorkerContextStatistics(
            idle_workers=len(self._worker_cache),
            running_workers=self._lifetime.calc_running(),
        )


# Exists on all platforms as single source of truth for kwarg defaults
DEFAULT_CONTEXT = WorkerContext._create()


def configure_default_context(
    idle_timeout=DEFAULT_CONTEXT.idle_timeout,
    init=DEFAULT_CONTEXT.init,
    retire=DEFAULT_CONTEXT.retire,
    grace_period=DEFAULT_CONTEXT.grace_period,
    worker_type=WorkerType.SPAWN,
):
    """Configure the default `WorkerContext` parameters associated with `run_sync`.

    Args:
      idle_timeout (float): The time in seconds an idle worker will
          wait for a CPU-bound job before shutting down and releasing its own
          resources. Pass `math.inf` to wait forever. MUST be non-negative.
      init (Callable[[], bool]):
          An object to call within the worker before waiting for jobs.
          This is suitable for initializing worker state so that such stateful logic
          does not need to be included in functions passed to
          :func:`trio_parallel.run_sync`. MUST be callable without arguments.
      retire (Callable[[], bool]):
          An object to call within the worker after executing a CPU-bound job.
          The return value indicates whether worker should be retired (shut down.)
          By default, workers are never retired.
          The process-global environment is stable between calls. Among other things,
          that means that storing state in global variables works.
          MUST be callable without arguments.
      grace_period (float): The time in seconds to wait in the atexit handler for
          workers to exit before issuing SIGKILL/TerminateProcess and raising
          `BrokenWorkerError`. Pass `math.inf` to wait forever. MUST be non-negative.
      worker_type (WorkerType): The kind of worker to create, see :class:`WorkerType`.

    Raises:
      RuntimeError: if this function is called outside the main thread.

    .. warning::

       This function is meant to be used once before any usage of `run_sync`.
       Doing otherwise may (on POSIX) result in workers being leaked until
       the main process ends, or (on Win32) having no effect until the next `trio.run`!
    """
    new_parm_dict = locals().copy()
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Only configure default context from the main thread")
    if sys.platform == "win32":
        try:
            DEFAULT_CONTEXT_RUNVAR.get()
        except (LookupError, RuntimeError):
            pass
        else:
            warnings.warn("Previous default context active until next `trio.run`")
        DEFAULT_CONTEXT_PARAMS.update(**new_parm_dict)
    else:
        stats = default_context_statistics()
        if stats.idle_workers or stats.running_workers:
            warnings.warn("Previous default context leaving zombie workers behind")
        # assign to a local for KI protection
        ctx = WorkerContext._create(**new_parm_dict)
        atexit.register(graceful_default_shutdown, ctx)
        global DEFAULT_CONTEXT
        DEFAULT_CONTEXT = ctx


if sys.platform == "win32":

    DEFAULT_CONTEXT_RUNVAR = trio.lowlevel.RunVar("win32_ctx")
    DEFAULT_CONTEXT_PARAMS = {}

    # TODO: intelligently test ki protection here such that CI fails if the
    #  decorators disappear

    @trio.lowlevel.enable_ki_protection
    def get_default_context():
        try:
            ctx = DEFAULT_CONTEXT_RUNVAR.get()
        except LookupError:
            ctx = WorkerContext._create(**DEFAULT_CONTEXT_PARAMS)
            DEFAULT_CONTEXT_RUNVAR.set(ctx)
            # KeyboardInterrupt here could leak the context
            trio.lowlevel.spawn_system_task(close_at_run_end, ctx)
        return ctx

    @trio.lowlevel.enable_ki_protection
    async def close_at_run_end(ctx):
        try:
            await trio.sleep_forever()
        finally:
            # KeyboardInterrupt here could leak the context
            await ctx._aclose(ctx.grace_period)  # noqa: TRIO102

else:

    def get_default_context():
        return DEFAULT_CONTEXT

    def graceful_default_shutdown(ctx):
        ctx._worker_cache.shutdown(ctx.grace_period)

    atexit.register(graceful_default_shutdown, DEFAULT_CONTEXT)


def default_context_statistics():
    """Return the statistics corresponding to the default context.

    Because the default context used by `trio_parallel.run_sync` is a private
    implementation detail, this function serves to provide public access to the default
    context statistics object.

    .. note::

       The statistics are only eventually consistent in the case of multiple trio
       threads concurrently using `trio_parallel.run_sync`."""
    return get_default_context().statistics()


@asynccontextmanager
@trio.lowlevel.enable_ki_protection
async def open_worker_context(
    idle_timeout=DEFAULT_CONTEXT.idle_timeout,
    init=DEFAULT_CONTEXT.init,
    retire=DEFAULT_CONTEXT.retire,
    grace_period=DEFAULT_CONTEXT.grace_period,
    worker_type=WorkerType.SPAWN,
):
    """Create a new, customized worker context with isolated workers.

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
      grace_period (float): The time in seconds to wait in ``__aexit__`` for workers to
          exit before issuing SIGKILL/TerminateProcess and raising `BrokenWorkerError`.
          Pass `math.inf` to wait forever. MUST be non-negative.
      worker_type (WorkerType): The kind of worker to create, see :class:`WorkerType`.

    Raises:
      ValueError | TypeError: if an invalid value is passed for an argument, such as a
          negative timeout.
      BrokenWorkerError: if a worker does not shut down cleanly when exiting the scope.

    .. warning::

       The callables passed to retire MUST not raise! Doing so will result in a
       :class:`BrokenWorkerError` at an indeterminate future
       :func:`WorkerContext.run_sync` call.

    """
    ctx = WorkerContext._create(idle_timeout, init, retire, grace_period, worker_type)
    try:
        yield ctx
    finally:
        await ctx._aclose()


def atexit_shutdown_grace_period(
    grace_period=DEFAULT_CONTEXT.grace_period,
):  # pragma: no cover, deprecated
    """Set the default worker cache shutdown grace period.

    DEPRECATION NOTICE: this function has been superseded by
    `configure_default_context` and will be removed in a future version

    You might need this if you have a long-running `atexit` function, such as those
    installed by ``coverage.py`` or ``viztracer``.
    This only affects the `atexit` behavior of the default context corresponding to
    :func:`trio_parallel.run_sync`. Existing and future `WorkerContext` instances
    are unaffected.

    Args:
      grace_period (float): The time in seconds to wait for workers to
          exit before issuing SIGKILL/TerminateProcess and raising `BrokenWorkerError`.
          Pass `math.inf` to wait forever.
    """
    import warnings

    warnings.warn(
        DeprecationWarning(
            "Superseded by `configure_default_context` "
            "and will be removed in a future version"
        )
    )
    configure_default_context(grace_period=grace_period)


async def run_sync(
    sync_fn: Callable[..., T],
    *args,
    cancellable: bool = False,
    limiter: trio.CapacityLimiter = None,
) -> T:
    """Run ``sync_fn(*args)`` in a separate process and return/raise its outcome.

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
    return await get_default_context().run_sync(
        sync_fn, *args, cancellable=cancellable, limiter=limiter
    )


if sys.platform == "win32":
    del DEFAULT_CONTEXT
