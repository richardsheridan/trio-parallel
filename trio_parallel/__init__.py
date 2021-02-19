"""Top-level package for trio-parallel."""

from ._version import __version__

from ._impl import (
    to_process_run_sync as run_sync,
    BrokenWorkerError,
    current_default_process_limiter,
)
