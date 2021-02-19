import multiprocessing
import os

import pytest
import trio

from .._impl import (
    WORKER_CACHE,
    to_process_run_sync,
    current_default_worker_limiter,
    WorkerProc,
)
from .._util import BrokenWorkerError
from trio.testing import wait_all_tasks_blocked


@pytest.fixture(autouse=True)
def empty_proc_cache():
    while True:
        try:
            proc = WORKER_CACHE.pop()
            proc.kill()
            proc._proc.join()
        except IndexError:
            return


def _echo_and_pid(x):  # pragma: no cover
    return (x, os.getpid())


def _raise_pid():  # pragma: no cover
    raise ValueError(os.getpid())


async def test_run_in_worker_process():
    trio_pid = os.getpid()
    limiter = trio.CapacityLimiter(1)

    x, child_pid = await to_process_run_sync(_echo_and_pid, 1, limiter=limiter)
    assert x == 1
    assert child_pid != trio_pid

    with pytest.raises(ValueError) as excinfo:
        await to_process_run_sync(_raise_pid, limiter=limiter)
    print(excinfo.value.args)
    assert excinfo.value.args[0] != trio_pid


def _block_proc_on_queue(q, ev, done_ev):  # pragma: no cover
    # Make the process block for a controlled amount of time
    ev.set()
    q.get()
    done_ev.set()


async def test_run_in_worker_process_cancellation(capfd):
    async def child(q, ev, done_ev, cancellable):
        print("start")
        try:
            return await to_process_run_sync(
                _block_proc_on_queue, q, ev, done_ev, cancellable=cancellable
            )
        finally:
            print("exit")

    m = multiprocessing.Manager()
    q = m.Queue()
    ev = m.Event()
    done_ev = m.Event()

    # This one can't be cancelled
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, q, ev, done_ev, False)
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        nursery.cancel_scope.cancel()
        with trio.CancelScope(shield=True):
            await wait_all_tasks_blocked(0.01)
        # It's still running
        assert not done_ev.is_set()
        q.put(None)
        # Now it exits

    ev = m.Event()
    done_ev = m.Event()
    # But if we cancel *before* it enters, the entry is itself a cancellation
    # point
    with trio.CancelScope() as scope:
        scope.cancel()
        await child(q, ev, done_ev, False)
    assert scope.cancelled_caught
    capfd.readouterr()

    ev = m.Event()
    done_ev = m.Event()
    # This is truly cancellable by killing the process
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, q, ev, done_ev, True)
        # Give it a chance to get started. (This is important because
        # to_thread_run_sync does a checkpoint_if_cancelled before
        # blocking on the thread, and we don't want to trigger this.)
        await wait_all_tasks_blocked()
        assert capfd.readouterr().out.rstrip() == "start"
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        # Then cancel it.
        nursery.cancel_scope.cancel()
    # The task exited, but the process died
    assert not done_ev.is_set()
    assert capfd.readouterr().out.rstrip() == "exit"


def _null_func():  # pragma: no cover
    pass


async def test_run_in_worker_process_fail_to_spawn(monkeypatch):
    from .. import _impl

    # Test the unlikely but possible case where trying to spawn a worker fails
    def bad_start(*a, **kw):
        raise RuntimeError("the engines canna take it captain")

    monkeypatch.setattr(_impl, "WorkerProc", bad_start)

    limiter = current_default_worker_limiter()
    assert limiter.borrowed_tokens == 0

    # We get an appropriate error, and the limiter is cleanly released
    with pytest.raises(RuntimeError) as excinfo:
        await to_process_run_sync(_null_func)  # pragma: no cover
    assert "engines" in str(excinfo.value)

    assert limiter.borrowed_tokens == 0


async def _null_async_fn():  # pragma: no cover
    pass


async def test_trio_to_process_run_sync_expected_error():
    with pytest.raises(TypeError, match="expected a sync function"):
        await to_process_run_sync(_null_async_fn)


def _segfault_out_of_bounds_pointer():  # pragma: no cover
    # https://wiki.python.org/moin/CrashingPython
    import ctypes

    i = ctypes.c_char(b"a")
    j = ctypes.pointer(i)
    c = 0
    while True:
        j[c] = b"a"
        c += 1


async def test_to_process_run_sync_raises_on_segfault():
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
            await to_process_run_sync(_segfault_out_of_bounds_pointer, cancellable=True)
    except BrokenWorkerError:
        pass
    except trio.TooSlowError:  # pragma: no cover
        pytest.xfail("Unable to cause segfault after 55 seconds.")
    else:  # pragma: no cover
        pytest.fail("No error was raised on segfault.")


def _never_halts(ev):  # pragma: no cover
    # important difference from blocking call is cpu usage
    ev.set()
    while True:
        pass


async def test_to_process_run_sync_cancel_infinite_loop():
    m = multiprocessing.Manager()
    ev = m.Event()

    async def child():
        await to_process_run_sync(_never_halts, ev, cancellable=True)

    async with trio.open_nursery() as nursery:
        nursery.start_soon(child)
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        nursery.cancel_scope.cancel()


async def test_to_process_run_sync_raises_on_kill():
    m = multiprocessing.Manager()
    ev = m.Event()

    async def child():
        await to_process_run_sync(_never_halts, ev, cancellable=True)

    await to_process_run_sync(_null_func)
    proc = WORKER_CACHE._cache[0]
    with pytest.raises(BrokenWorkerError):
        with trio.move_on_after(10):
            async with trio.open_nursery() as nursery:
                nursery.start_soon(child)
                try:
                    await trio.to_thread.run_sync(ev.wait, cancellable=True)
                finally:
                    # if something goes wrong, free the thread
                    ev.set()
                proc.kill()  # also tests multiple calls to proc.kill


async def test_wake_worker_in_thread_and_prune_cache():
    # make sure we can successfully put worker waking in a Trio thread
    proc = WorkerProc()
    await trio.to_thread.run_sync(proc.wake_up)
    # take it's number and kill it for the next test
    pid1 = proc._proc.pid
    proc.kill()
    with trio.fail_after(1):
        await proc.wait()
    # put dead proc into the cache (normal code never does this)
    WORKER_CACHE.push(proc)
    # dead procs shouldn't pop out
    with pytest.raises(IndexError):
        WORKER_CACHE.pop()
    WORKER_CACHE.push(proc)
    # should spawn a new worker and remove the dead one
    _, pid2 = await to_process_run_sync(_echo_and_pid, None)
    assert len(WORKER_CACHE) == 1
    assert pid1 != pid2


async def test_to_process_run_sync_large_job():
    n = 2 ** 20
    x, _ = await to_process_run_sync(_echo_and_pid, bytearray(n))
    assert len(x) == n


async def test_exhaustively_cancel_run_sync():
    # to test that cancellation does not ever leave a living process behind
    # currently requires manually targeting all but last checkpoints
    m = multiprocessing.Manager()
    ev = m.Event()

    # cancel at job send
    proc = WorkerProc()
    proc.wake_up()
    with trio.fail_after(1):
        with trio.move_on_after(0):
            await proc.run_sync(_never_halts, ev)
        await proc.wait()

    # cancel at result recv is tested elsewhere
