"""Abstract base classes for internal use when implementing future workers

The idea is that if we keep the interface between the implementation of the
trio-parallel API minimal, we can put in new workers and options without needing
frontend rewrites."""

from abc import ABC, abstractmethod, ABCMeta
from typing import Optional, Callable, TypeVar, Type, Any, Deque

from outcome import Outcome


MAX_TIMEOUT = 24.0 * 60.0 * 60.0


class BrokenWorkerError(RuntimeError):
    """Raised when a worker fails or dies unexpectedly.

    This error is not typically encountered in normal use, and indicates a severe
    failure of either trio-parallel or the code that was executing in the worker.
    Some example failures may include segfaults, being killed by an external signal,
    or failing to cleanly shut down within a specified ``grace_period``. (See
    :func:`atexit_shutdown_grace_period` and :func:`open_worker_context`.)
    """


class AbstractWorker(ABC):
    @abstractmethod
    def __init__(
        self,
        idle_timeout: float,
        init: Optional[Callable[[], bool]],
        retire: Optional[Callable[[], bool]],
    ):
        pass

    @abstractmethod
    async def start(self):
        """Perform async startup tasks that really should be in init."""

    @abstractmethod
    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        """Run the sync_fn in a worker.

        Args:
          sync_fn: A synchronous callable.
          *args: Positional arguments to pass to sync_fn. If you need keyword
              arguments, use :func:`functools.partial`.

        Returns:
          Optional[Outcome]: The outcome of the CPU bound job performed in the
              worker, or ``None``, indicating the work should be submitted again,
              but to a different worker, because this worker should be discarded.

        Raises:
          BrokenWorkerError: Indicates the worker died unexpectedly. Not encountered
              in normal use."""

    @abstractmethod
    def shutdown(self):
        """Trigger a graceful shutdown of the worker.

        :meth:`run_sync` will return None in response to any future job submissions.
        Jobs in progress will complete as normal."""

    @abstractmethod
    async def wait(self):
        """Wait for the worker to terminate."""


class WorkerCache(Deque[AbstractWorker], ABC):
    @abstractmethod
    def prune(self):
        """Clean up any resources associated with workers that have timed out
        while idle in the cache."""

    @abstractmethod
    def shutdown(self, timeout):
        """Stop and clean up any resources associated with all cached workers.

        Args:
          timeout: Time in seconds to wait for graceful shutdown before
              raising.

        Raises:
          BrokenWorkerError: Raised if any workers fail to respond to a graceful
              shutdown signal within ``grace_period``."""


# vendored from trio so that we can lazy import trio
T = TypeVar("T")


class NoPublicConstructor(ABCMeta):
    """Metaclass that ensures a private constructor.

    If a class uses this metaclass like this::

        class SomeClass(metaclass=NoPublicConstructor):
            pass

    The metaclass will ensure that no sub class can be created, and that no instance
    can be initialized.

    If you try to instantiate your class (SomeClass()), a TypeError will be thrown.

    Raises
    ------
    - TypeError if a sub class or an instance is created.
    """

    def __call__(cls, *args, **kwargs):
        raise TypeError(
            f"{cls.__module__}.{cls.__qualname__} has no public constructor"
        )

    def _create(cls: Type[T], *args: Any, **kwargs: Any) -> T:
        return super().__call__(*args, **kwargs)  # type: ignore
