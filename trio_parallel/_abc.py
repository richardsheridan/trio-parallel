"""Abstract base classes for internal use when implementing future workers

The idea is that if we keep the interface between the implementation of the
trio-parallel API minimal, we can put in new workers and options without needing
frontend rewrites."""

from abc import ABC, abstractmethod
from collections import deque
from typing import Optional, Callable

from outcome import Outcome


class BrokenWorkerError(RuntimeError):
    """Raised when a worker fails or dies unexpectedly.

    This error is not typically encountered in normal use, and indicates a severe
    failure of either trio-parallel or the code that was executing in the worker.
    """


class WorkerCache(deque, ABC):
    @abstractmethod
    def prune(self):
        """Clean up any resources associated with workers that have timed out
        while idle in the cache."""

    @abstractmethod
    async def clear(self):
        """Stop and clean up any resources associated with all cached workers."""


class AbstractWorker(ABC):
    @abstractmethod
    def __init__(self, idle_timeout: float, retire: Optional[Callable[[], bool]]):
        pass

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
              in normal use.

        """
