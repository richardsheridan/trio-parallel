""" Tests of internal worker process API ("contract" tests)

    These are specific to subprocesses and you wouldn't expect these to pass
    with thread or subinterpreter workers.
"""


import trio
import pytest

from .._proc import WORKER_PROC_MAP
from .._abc import BrokenWorkerError


@pytest.fixture(params=list(WORKER_PROC_MAP.values()), ids=list(WORKER_PROC_MAP.keys()))
async def worker(request):
    worker = request.param[0](None, bool, bool)
    try:
        yield worker
    finally:
        with trio.move_on_after(2) as cs:
            worker.shutdown()
            await worker.wait()
        if cs.cancelled_caught:
            with trio.fail_after(1):  # pragma: no cover, leads to failure case
                worker.kill()
                await worker.wait()
                pytest.fail(
                    "tests should be responsible for killing and waiting if "
                    "they do not lead to a graceful shutdown state"
                )


def _never_halts(ev):  # pragma: no cover, worker will be killed
    # important difference from blocking call is cpu usage
    ev.set()
    while True:
        pass


async def test_run_sync_cancel_infinite_loop(worker, manager):
    ev = manager.Event()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(worker.run_sync, _never_halts, ev)
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        nursery.cancel_scope.cancel()
    with trio.fail_after(1):
        assert await worker.wait() in (-15, -9, 255)  # 255 for py3.6 forkserver


async def test_run_sync_raises_on_kill(worker, manager):
    ev = manager.Event()
    await worker.run_sync(int)  # running start so actual test is less racy
    with pytest.raises(BrokenWorkerError) as exc_info, trio.fail_after(5):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(worker.run_sync, _never_halts, ev)
            await trio.to_thread.run_sync(ev.wait, cancellable=True)
            worker.kill()  # also tests multiple calls to worker.kill
    exitcode = await worker.wait()
    assert exitcode in (-15, -9, 255)  # 255 for py3.6 forkserver
    assert exc_info.value.args[-1].exitcode == exitcode


def _segfault_out_of_bounds_pointer():  # pragma: no cover, worker will be killed
    # https://wiki.python.org/moin/CrashingPython
    import ctypes

    i = ctypes.c_char(b"a")
    j = ctypes.pointer(i)
    c = 0
    while True:
        j[c] = i
        c += 1


async def test_run_sync_raises_on_segfault(worker, capfd):
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
            await worker.run_sync(_segfault_out_of_bounds_pointer)
    except BrokenWorkerError as e:
        exitcode = await worker.wait()
        assert exitcode  # not sure if we expect a universal value, but not 0 or None
        assert e.args[-1].exitcode == exitcode
    except trio.TooSlowError:  # pragma: no cover
        pytest.xfail("Unable to cause segfault after 55 seconds.")
    else:  # pragma: no cover
        pytest.fail("No error was raised on segfault.")


# to test that cancellation does not ever leave a living process behind
# currently requires manually targeting all but last checkpoints


async def test_exhaustively_cancel_run_sync1(worker):
    if worker.mp_context._name == "fork":
        pytest.skip("Doesn't exist on WorkerForkProc")
    # cancel at startup
    with trio.fail_after(1):
        with trio.move_on_after(0) as cs:
            assert (await worker.run_sync(int)).unwrap()  # will return zero
        assert cs.cancelled_caught
        assert await worker.wait() is None


async def test_exhaustively_cancel_run_sync2(worker, manager):
    # cancel at job send if we reuse the worker
    ev = manager.Event()
    await worker.run_sync(int)
    with trio.fail_after(1):
        with trio.move_on_after(0):
            await worker.run_sync(_never_halts, ev)
        assert await worker.wait() in (-15, -9, 255)  # 255 for py3.6 forkserver

    # cancel at result recv is tested elsewhere


def _raise_ki():
    import signal

    trio._util.signal_raise(signal.SIGINT)


async def test_ki_does_not_propagate(worker):
    (await worker.run_sync(_raise_ki)).unwrap()


_lambda = lambda: None  # pragma: no cover


def _return_lambda():
    return _lambda


@pytest.mark.parametrize("job", [_lambda, _return_lambda])
async def test_unpickleable(job, worker):
    from pickle import PicklingError

    with pytest.raises((PicklingError, AttributeError)):
        (await worker.run_sync(job)).unwrap()
