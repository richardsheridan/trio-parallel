import multiprocessing
import os

import pytest
import trio

from .._abc import BrokenWorkerError
from .._impl import DEFAULT_CONTEXT, WorkerType, run_sync, cache_scope


@pytest.fixture(autouse=True)
def empty_proc_cache():
    while True:
        try:
            proc = DEFAULT_CONTEXT.worker_cache.pop()
            proc.kill()
            proc._proc.join()
        except IndexError:
            return


@pytest.fixture(scope="module")
def manager():
    m = multiprocessing.get_context("spawn").Manager()
    with m:
        yield m


def _raise_pid():  # pragma: no cover
    raise ValueError(os.getpid())


async def test_run_sync():
    trio_pid = os.getpid()
    limiter = trio.CapacityLimiter(1)

    child_pid = await run_sync(os.getpid, limiter=limiter)
    assert child_pid != trio_pid

    with pytest.raises(ValueError) as excinfo:
        await run_sync(_raise_pid, limiter=limiter)

    assert excinfo.value.args[0] != trio_pid


def _block_proc(block, start, done):  # pragma: no cover
    # Make the process block for a controlled amount of time
    start.set()
    block.wait()
    done.set()


async def test_cancellation(manager):
    async def child(cancellable):
        nonlocal child_start, child_done
        child_start = True
        try:
            return await run_sync(
                _block_proc, block, proc_start, proc_done, cancellable=cancellable
            )
        finally:
            child_done = True

    block, proc_start, proc_done = manager.Event(), manager.Event(), manager.Event()
    child_start = False
    child_done = False

    # This one can't be cancelled
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, False)
        await trio.to_thread.run_sync(proc_start.wait, cancellable=True)
        assert child_start
        nursery.cancel_scope.cancel()
        with trio.CancelScope(shield=True):
            await trio.testing.wait_all_tasks_blocked(0.01)
        # It's still running
        assert not proc_done.is_set()
        block.set()
        # Now it exits
    assert child_done
    assert proc_done.is_set()

    block.clear()
    proc_start.clear()
    proc_done.clear()
    child_start = False
    child_done = False
    # But if we cancel *before* it enters, the entry is itself a cancellation
    # point
    with trio.CancelScope() as scope:
        scope.cancel()
        await child(False)
    assert scope.cancelled_caught
    assert child_start
    assert child_done
    assert not proc_start.is_set()
    assert not proc_done.is_set()

    block.clear()
    proc_start.clear()
    proc_done.clear()
    child_start = False
    child_done = False
    # This is truly cancellable by killing the process
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, True)
        # Give it a chance to get started. (This is important because
        # to_thread_run_sync does a checkpoint_if_cancelled before
        # blocking on the thread, and we don't want to trigger this.)
        await trio.testing.wait_all_tasks_blocked(0.01)
        assert child_start
        with trio.fail_after(1):
            await trio.to_thread.run_sync(proc_start.wait, cancellable=True)
        # Then cancel it.
        nursery.cancel_scope.cancel()
    # The task exited, but the process died
    assert not block.is_set()
    assert not proc_done.is_set()
    assert child_done


async def _null_async_fn():  # pragma: no cover
    pass


async def test_run_sync_coroutine_error():
    with pytest.raises(TypeError, match="expected a sync function"):
        await run_sync(_null_async_fn)


async def test_prune_cache():
    # take proc's number and kill it for the next test
    pid1 = await run_sync(os.getpid)
    proc = DEFAULT_CONTEXT.worker_cache.pop()
    proc.kill()
    with trio.fail_after(1):
        await proc.wait()
    pid2 = await run_sync(os.getpid)
    # put dead proc into the cache (normal code never does this)
    DEFAULT_CONTEXT.worker_cache.appendleft(proc)
    pid2 = await run_sync(os.getpid)
    assert len(DEFAULT_CONTEXT.worker_cache) == 1
    assert pid1 != pid2


async def test_run_sync_large_job():
    n = 2 ** 20
    x = await run_sync(bytes, bytearray(n))
    assert len(x) == n


async def test_cache_scope():
    pid1 = await run_sync(os.getpid)
    with cache_scope():
        pid2 = await run_sync(os.getpid)
        assert pid1 != pid2
    pid5 = await run_sync(os.getpid)
    assert pid5 == pid1


_NUM_RUNS = 0


def _retire_run_twice():  # pragma: no cover
    global _NUM_RUNS
    if _NUM_RUNS >= 2:
        return True
    else:
        _NUM_RUNS += 1
        return False


async def test_cache_retire():
    with cache_scope(retire=_retire_run_twice):
        pid2 = await run_sync(os.getpid)
        pid3 = await run_sync(os.getpid)
        assert pid3 == pid2
        pid4 = await run_sync(os.getpid)
        assert pid4 != pid2


async def test_cache_timeout():
    with trio.fail_after(20):
        with cache_scope(idle_timeout=0):
            pid0 = await run_sync(os.getpid)
            while pid0 == await run_sync(os.getpid):
                pass  # pragma: no cover, rare race will reuse proc once or twice


@pytest.mark.parametrize("method", list(WorkerType))
async def test_cache_type(method):
    with cache_scope(worker_type=method):
        assert 0 == await run_sync(int)


async def test_erroneous_scope_inputs():
    with pytest.raises(ValueError):
        with cache_scope(idle_timeout=-1):
            pass
    with pytest.raises(ValueError):
        with cache_scope(retire=0):
            pass
    with pytest.raises(ValueError):
        with cache_scope(worker_type="wrong"):
            pass


def test_not_in_async_context():
    with pytest.raises(RuntimeError):
        with cache_scope():
            assert False, "__enter__ should raise"


def _bad_retire_fn():
    assert False


async def test_bad_retire_fn(capfd):
    with pytest.raises(BrokenWorkerError):
        with trio.fail_after(1):
            with cache_scope(retire=_bad_retire_fn):
                await run_sync(os.getpid, cancellable=True)
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


async def test_truthy_retire_fn_can_be_cancelled():
    with trio.move_on_after(0.1) as cs:
        with cache_scope(retire=object):
            assert await run_sync(int)

    assert cs.cancelled_caught
