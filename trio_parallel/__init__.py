"""Top-level package for trio-parallel."""

from importlib_metadata import version

__version__ = version("trio-parallel")
del version

from ._impl import (
    run_sync,
    cache_scope,
    current_default_worker_limiter,
)
from ._abc import BrokenWorkerError
