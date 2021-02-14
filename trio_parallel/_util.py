class BrokenWorkerError(RuntimeError):
    """Raised when a worker  fails or dies unexpectedly.

    This error is not typically encountered in normal use, and indicates a severe
    failure of either trio-parallel or the code that was executing in the worker.
    """

    pass
