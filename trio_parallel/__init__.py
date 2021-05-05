"""Top-level package for trio-parallel."""

from ._impl import (
    to_process_run_sync as run_sync,
    current_default_worker_limiter,
)
from ._proc import BrokenWorkerError
