"""Top-level package for trio-parallel."""

try:
    from ._version import __version__
except ImportError:
    import warnings
    warnings.warn("trio_parallel._version module missing, continuing without __version__")

from ._impl import (
    to_process_run_sync as run_sync,
    current_default_worker_limiter,
)
from ._util import BrokenWorkerError
