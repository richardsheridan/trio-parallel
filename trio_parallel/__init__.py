"""Top-level package for trio-parallel."""

from ._impl import (
    to_process_run_sync as run_sync,
    current_default_worker_limiter,
)
from ._util import BrokenWorkerError

from ._version import get_versions

__version__ = get_versions()["version"]
del get_versions
