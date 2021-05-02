import multiprocessing
import signal
import time

import trio
import pytest

from .._proc import WorkerProc, BrokenWorkerError


@pytest.fixture
async def proc():
    proc = WorkerProc()
    try:
        yield proc
    finally:
        proc.kill()
        with trio.fail_after(1):
            await proc.wait()


@pytest.fixture(scope="module")
def manager():
    m = multiprocessing.get_context("spawn").Manager()
    with m:
        yield m


def _never_halts(ev):  # pragma: no cover
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


# TODO: debug manager interaction with pipes on PyPy GH#44
async def test_run_sync_raises_on_kill(proc):
    await proc.run_sync(int)  # running start so actual test is less racy
    with pytest.raises(BrokenWorkerError) as exc_info, trio.fail_after(5):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(proc.run_sync, time.sleep, 5)
            await trio.sleep(0.1)
            proc.kill()  # also tests multiple calls to proc.kill
    assert exc_info.value.args[-1].exitcode is not None


def _segfault_out_of_bounds_pointer():  # pragma: no cover
    # https://wiki.python.org/moin/CrashingPython
    import ctypes

    i = ctypes.c_char(b"a")
    j = ctypes.pointer(i)
    c = 0
    while True:
        j[c] = i
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
    except BrokenWorkerError as e:
        assert e.args[-1].exitcode is not None
    except trio.TooSlowError:  # pragma: no cover
        pytest.xfail("Unable to cause segfault after 55 seconds.")
    else:  # pragma: no cover
        pytest.fail("No error was raised on segfault.")


# to test that cancellation does not ever leave a living process behind
# currently requires manually targeting all but last checkpoints


async def test_exhaustively_cancel_run_sync1(proc):
    # cancel at startup
    with trio.fail_after(1):
        with trio.move_on_after(0):
            assert (await proc.run_sync(int)).unwrap()  # will return zero
        await proc.wait()


async def test_exhaustively_cancel_run_sync2(proc, manager):
    # cancel at job send if we reuse the process
    ev = manager.Event()
    await proc.run_sync(int)
    with trio.fail_after(1):
        with trio.move_on_after(0):
            await proc.run_sync(_never_halts, ev)

    # cancel at result recv is tested elsewhere


def _shorten_timeout():  # pragma: no cover
    from .. import _proc

    _proc.IDLE_TIMEOUT = 0


async def test_racing_timeout(proc):
    await proc.run_sync(_shorten_timeout)
    with trio.fail_after(1):
        while (await proc.run_sync(int)) is not None:
            pass  # pragma: no cover, this rarely takes more than one iteration.
    with trio.fail_after(1):
        await proc.wait()


def _raise_ki():  # pragma: no cover
    trio._util.signal_raise(signal.SIGINT)


async def test_ki_does_not_propagate(proc):
    await proc.run_sync(_raise_ki)


async def test_clean_exit_on_pipe_close(proc, capfd):
    # This could happen on weird __del__/weakref/atexit situations.
    # It was not visible on normal, clean exits because multiprocessing
    # would call terminate before pipes were GC'd.
    x = await proc.run_sync(int)
    x.unwrap()
    proc._send_pipe.close()
    proc._recv_pipe.close()
    with trio.fail_after(1):
        assert 0 == await proc.wait()

    out, err = capfd.readouterr()
    assert not out
    assert not err
