"""All functions run in subprocesses should be in here so they don't need to import
trio, which adds a long time to every subprocess startup."""

import os
import sys


def _bad_retire_fn():
    assert False


_NUM_RUNS_LEFT = 0


def _init_run_twice():
    global _NUM_RUNS_LEFT
    _NUM_RUNS_LEFT = 2


def _retire_run_twice():
    global _NUM_RUNS_LEFT
    _NUM_RUNS_LEFT -= 1
    return _NUM_RUNS_LEFT <= 0


def _delayed_bad_retire_fn():
    if _retire_run_twice():
        _bad_retire_fn()


def _loopy_retire_fn():  # pragma: no cover, will be killed
    if _retire_run_twice():
        import time

        while True:
            time.sleep(1)


def _raise_pid():
    raise ValueError(os.getpid())


def _block_worker(block, start, done):
    # Make the worker block for a controlled amount of time
    start.set()
    block.wait()
    done.set()


def _never_halts(ev):  # pragma: no cover, worker will be killed
    # important difference from blocking call is cpu usage
    ev.set()
    while True:
        pass


def _segfault_out_of_bounds_pointer():  # pragma: no cover, worker will be killed
    # https://wiki.python.org/moin/CrashingPython
    import ctypes

    i = ctypes.c_char(b"a")
    j = ctypes.pointer(i)
    c = 0
    while True:
        j[c] = i
        c += 1


def _raise_ki():
    import signal, trio

    trio._util.signal_raise(signal.SIGINT)


_lambda = lambda: None  # pragma: no cover, never run


def _return_lambda():
    return _lambda


async def _null_async_fn():  # pragma: no cover, coroutine called but not run
    pass


def _monkeypatch_max_timeout():
    from .. import _abc

    _abc.MAX_TIMEOUT = 0.1
    return True


def _no_trio():
    return "trio" not in sys.modules
