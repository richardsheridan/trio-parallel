import multiprocessing
import os

import pytest
import trio

from .._impl import WORKER_CACHE, to_process_run_sync


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
    return x, os.getpid()


def _raise_pid():  # pragma: no cover
    raise ValueError(os.getpid())


async def test_run_in_worker():
    trio_pid = os.getpid()
    limiter = trio.CapacityLimiter(1)

    x, child_pid = await to_process_run_sync(_echo_and_pid, 1, limiter=limiter)
    assert x == 1
    assert child_pid != trio_pid

    with pytest.raises(ValueError) as excinfo:
        await to_process_run_sync(_raise_pid, limiter=limiter)

    assert excinfo.value.args[0] != trio_pid


def _block_proc(block, start, done):  # pragma: no cover
    # Make the process block for a controlled amount of time
    start.set()
    block.wait()
    done.set()


async def test_cancellation(capfd):
    async def child(cancellable):
        print("start")
        try:
            return await to_process_run_sync(
                _block_proc, block, start, done, cancellable=cancellable
            )
        finally:
            print("exit")

    m = multiprocessing.Manager()
    block, start, done = m.Event(), m.Event(), m.Event()

    # This one can't be cancelled
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, False)
        await trio.to_thread.run_sync(start.wait, cancellable=True)
        assert capfd.readouterr().out.rstrip() == "start"
        nursery.cancel_scope.cancel()
        with trio.CancelScope(shield=True):
            await trio.testing.wait_all_tasks_blocked(0.01)
        # It's still running
        assert not done.is_set()
        block.set()
        # Now it exits
    assert capfd.readouterr().out.rstrip() == "exit"

    block.clear()
    start.clear()
    done.clear()
    # But if we cancel *before* it enters, the entry is itself a cancellation
    # point
    with trio.CancelScope() as scope:
        scope.cancel()
        await child(False)
    assert capfd.readouterr().out.rstrip() == "start\nexit"
    assert scope.cancelled_caught

    block.clear()
    start.clear()
    done.clear()
    # This is truly cancellable by killing the process
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, True)
        # Give it a chance to get started. (This is important because
        # to_thread_run_sync does a checkpoint_if_cancelled before
        # blocking on the thread, and we don't want to trigger this.)
        await trio.testing.wait_all_tasks_blocked(0.01)
        assert capfd.readouterr().out.rstrip() == "start"
        await trio.to_thread.run_sync(start.wait, cancellable=True)
        # Then cancel it.
        nursery.cancel_scope.cancel()
    # The task exited, but the process died
    assert not done.is_set()
    assert capfd.readouterr().out.rstrip() == "exit"


async def _null_async_fn():  # pragma: no cover
    pass


async def test_raises_on_async_fn():
    with pytest.raises(TypeError, match="expected a sync function"):
        await to_process_run_sync(_null_async_fn)


async def test_prune_cache():
    # take proc's number and kill it for the next test
    _, pid1 = await to_process_run_sync(_echo_and_pid, None)
    proc = WORKER_CACHE.pop()
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


async def test_large_job():
    n = 2 ** 20
    x, _ = await to_process_run_sync(_echo_and_pid, bytearray(n))
    assert len(x) == n
