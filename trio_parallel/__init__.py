"""Top-level package for trio-parallel."""

from importlib_metadata import version

__version__ = version("trio-parallel")
del version

from ._impl import (
    to_process_run_sync as run_sync,
    current_default_worker_limiter,
)
from ._proc import BrokenWorkerError
