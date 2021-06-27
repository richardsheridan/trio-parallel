import multiprocessing
import signal
import time

import trio
import pytest

from .._proc import WORKER_PROC_MAP
from .._abc import BrokenWorkerError


@pytest.fixture(params=list(WORKER_PROC_MAP.values()), ids=list(WORKER_PROC_MAP.keys()))
async def proc(request):
    proc = request.param[0](600, bool)
    try:
        yield proc
    finally:
        with trio.move_on_after(0.01) as cs:
            await proc.wait()
        if not cs.cancelled_caught:
            return
        with trio.move_on_after(2) as cs:
            proc._send_pipe.close()
            await proc.wait()
        if not cs.cancelled_caught:  # pragma: no branch, leads to failure case
            return
        with trio.fail_after(1):  # pragma: no cover, leads to failure case
            proc.kill()
            await proc.wait()
            pytest.fail(
                "tests should be responsible for killing and waiting if they do not lead to "
                "a graceful shutdown state"
            )


@pytest.fixture(scope="module")
def manager():
    m = multiprocessing.get_context("spawn").Manager()
    with m:
        yield m


def _never_halts(ev):  # pragma: no cover, proc will be killed
    # important difference from blocking call is cpu usage
    ev.set()
    while True:
        pass


async def test_run_sync_cancel_infinite_loop(proc, manager):
    ev = manager.Event()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(proc.run_sync, _never_halts, ev)
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        nursery.cancel_scope.cancel()
    with trio.fail_after(1):
        assert await proc.wait() in (-15, -9, 255)  # 255 for py3.6 forkserver


# TODO: debug manager interaction with pipes on PyPy GH#44
async def test_run_sync_raises_on_kill(proc):
    await proc.run_sync(int)  # running start so actual test is less racy
    with pytest.raises(BrokenWorkerError) as exc_info, trio.fail_after(5):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(proc.run_sync, time.sleep, 5)
            await trio.sleep(0.1)
            proc.kill()  # also tests multiple calls to proc.kill
    exitcode = await proc.wait()
    assert exitcode in (-15, -9, 255)  # 255 for py3.6 forkserver
    assert exc_info.value.args[-1].exitcode == exitcode


def _segfault_out_of_bounds_pointer():  # pragma: no cover, proc will be killed
    # https://wiki.python.org/moin/CrashingPython
    import ctypes

    i = ctypes.c_char(b"a")
    j = ctypes.pointer(i)
    c = 0
    while True:
        j[c] = i
        c += 1


async def test_run_sync_raises_on_segfault(proc, capfd):
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
    except BrokenWorkerError as e:
        exitcode = await proc.wait()
        assert exitcode  # not sure if we expect a universal value, but not 0 or None
        assert e.args[-1].exitcode == exitcode
    except trio.TooSlowError:  # pragma: no cover
        pytest.xfail("Unable to cause segfault after 55 seconds.")
    else:  # pragma: no cover
        pytest.fail("No error was raised on segfault.")


# to test that cancellation does not ever leave a living process behind
# currently requires manually targeting all but last checkpoints


async def test_exhaustively_cancel_run_sync1(proc):
    if proc.mp_context._name == "fork":
        proc.kill()  # satisfy fixture wait() check
        pytest.skip("Doesn't exist on WorkerForkProc")
    # cancel at startup
    with trio.fail_after(1):
        with trio.move_on_after(0) as cs:
            assert (await proc.run_sync(int)).unwrap()  # will return zero
        assert cs.cancelled_caught
        assert await proc.wait() is None


async def test_exhaustively_cancel_run_sync2(proc, manager):
    # cancel at job send if we reuse the process
    ev = manager.Event()
    await proc.run_sync(int)
    with trio.fail_after(1):
        with trio.move_on_after(0):
            await proc.run_sync(_never_halts, ev)
        assert await proc.wait() in (-15, -9, 255)  # 255 for py3.6 forkserver

    # cancel at result recv is tested elsewhere


def _raise_ki():
    trio._util.signal_raise(signal.SIGINT)


async def test_ki_does_not_propagate(proc):
    (await proc.run_sync(_raise_ki)).unwrap()


async def test_clean_exit_on_pipe_close(proc, capfd):
    # This could happen on weird __del__/weakref/atexit situations.
    # It was not visible on normal, clean exits because multiprocessing
    # would call terminate before pipes were GC'd.
    x = await proc.run_sync(int)
    x.unwrap()
    proc._send_pipe.close()
    proc._recv_pipe.close()
    with trio.fail_after(1):
        assert await proc.wait() == 0

    out, err = capfd.readouterr()
    assert not out
    assert not err
