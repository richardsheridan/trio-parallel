import multiprocessing
import os

import trio
import pytest

from .._util import BrokenWorkerError
from .._proc import WorkerProc


@pytest.fixture
async def proc():
    proc = WorkerProc()
    await trio.to_thread.run_sync(proc.wake_up)
    yield proc
    with trio.fail_after(1):
        await proc.wait()


def _echo_and_pid(x):  # pragma: no cover
    return (x, os.getpid())


def _never_halts(ev):  # pragma: no cover
    # important difference from blocking call is cpu usage
    ev.set()
    while True:
        pass


async def test_run_sync_cancel_infinite_loop(proc):
    m = multiprocessing.Manager()
    ev = m.Event()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(proc.run_sync, _never_halts, ev)
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        nursery.cancel_scope.cancel()


async def test_run_sync_raises_on_kill(proc):
    m = multiprocessing.Manager()
    ev = m.Event()

    with pytest.raises(BrokenWorkerError):
        with trio.move_on_after(10):
            async with trio.open_nursery() as nursery:
                nursery.start_soon(proc.run_sync, _never_halts, ev)
                try:
                    await trio.to_thread.run_sync(ev.wait, cancellable=True)
                finally:
                    # if something goes wrong, free the thread
                    ev.set()
                proc.kill()  # also tests multiple calls to proc.kill


def _segfault_out_of_bounds_pointer():  # pragma: no cover
    # https://wiki.python.org/moin/CrashingPython
    import ctypes

    i = ctypes.c_char(b"a")
    j = ctypes.pointer(i)
    c = 0
    while True:
        j[c] = b"a"
        c += 1


async def test_run_sync_raises_on_segfault(proc):
    # This test was flaky on CI across several platforms and implementations.
    # I can reproduce it locally if there is some other process using the rest
    # of the CPU (F@H in this case) although I cannot explain why running this
    # on a busy machine would change the number of iterations (40-50k) needed
    # for the OS to notice there is something funny going on with memory access.
    # The usual symptom was for the segfault to occur, but the process
    # to fail to raise the error for more than one minute, which would
    # stall the test runner for 10 minutes.
    # Here we raise our own failure error before the test runner timeout (55s)
    # but xfail if we actually have to timeout.
    try:
        with trio.fail_after(55):
            await proc.run_sync(_segfault_out_of_bounds_pointer)
    except BrokenWorkerError:
        pass
    except trio.TooSlowError:  # pragma: no cover
        pytest.xfail("Unable to cause segfault after 55 seconds.")
    else:  # pragma: no cover
        pytest.fail("No error was raised on segfault.")


async def test_exhaustively_cancel_run_sync(proc):
    # to test that cancellation does not ever leave a living process behind
    # currently requires manually targeting all but last checkpoints
    m = multiprocessing.Manager()
    ev = m.Event()

    # cancel at job send
    with trio.fail_after(1):
        with trio.move_on_after(0):
            await proc.run_sync(_never_halts, ev)
        await proc.wait()

    # cancel at result recv is tested elsewhere
