import inspect
import multiprocessing
import os
import sys

import pytest
import trio

from .._abc import BrokenWorkerError
from .._impl import DEFAULT_CONTEXT, WorkerType, run_sync, cache_scope


@pytest.fixture(autouse=True)
def shutdown_cache():
    trio.run(DEFAULT_CONTEXT.worker_cache.shutdown)
    DEFAULT_CONTEXT.worker_cache.clear()


@pytest.fixture(scope="module")
def manager():
    m = multiprocessing.get_context("spawn").Manager()
    with m:
        yield m


def _raise_pid():
    raise ValueError(os.getpid())


async def test_run_sync():
    trio_pid = os.getpid()
    limiter = trio.CapacityLimiter(1)

    child_pid = await run_sync(os.getpid, limiter=limiter)
    assert child_pid != trio_pid

    with pytest.raises(ValueError) as excinfo:
        await run_sync(_raise_pid, limiter=limiter)

    assert excinfo.value.args[0] != trio_pid


def _block_worker(block, start, done):
    # Make the worker block for a controlled amount of time
    start.set()
    block.wait()
    done.set()


async def test_cancellation(manager):
    async def child(cancellable):
        nonlocal child_start, child_done
        child_start = True
        try:
            return await run_sync(
                _block_worker, block, worker_start, worker_done, cancellable=cancellable
            )
        finally:
            child_done = True

    block, worker_start, worker_done = manager.Event(), manager.Event(), manager.Event()
    child_start = False
    child_done = False

    # If we cancel *before* it enters, the entry is itself a cancellation
    # point
    with trio.CancelScope() as scope:
        scope.cancel()
        await child(False)
    assert scope.cancelled_caught
    assert child_start
    assert child_done
    assert not worker_start.is_set()
    assert not worker_done.is_set()

    block.clear()
    worker_start.clear()
    worker_done.clear()
    child_start = False
    child_done = False
    # prime worker cache so fail timeout doesn't have to be so long
    await run_sync(bool)
    # This is truly cancellable by killing the worker
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, True)
        # Give it a chance to get started. (This is important because
        # to_thread_run_sync does a checkpoint_if_cancelled before
        # blocking on the thread, and we don't want to trigger this.)
        await trio.testing.wait_all_tasks_blocked(0.01)
        assert child_start
        with trio.fail_after(1):
            await trio.to_thread.run_sync(worker_start.wait, cancellable=True)
        # Then cancel it.
        nursery.cancel_scope.cancel()
    # The task exited, but the worker died
    assert not block.is_set()
    assert not worker_done.is_set()
    assert child_done

    block.clear()
    worker_start.clear()
    worker_done.clear()
    child_start = False
    child_done = False

    # This one can't be cancelled
    async with trio.open_nursery() as nursery:
        nursery.start_soon(child, False)
        await trio.to_thread.run_sync(worker_start.wait, cancellable=True)
        assert child_start
        nursery.cancel_scope.cancel()
        with trio.CancelScope(shield=True):
            await trio.testing.wait_all_tasks_blocked(0.01)
        # It's still running
        assert not worker_done.is_set()
        block.set()
        # Now it exits
    assert child_done
    assert worker_done.is_set()


async def _null_async_fn():  # pragma: no cover, coroutine called but not run
    pass


async def test_run_sync_coroutine_error():
    with pytest.raises(TypeError, match="expected a sync function"):
        await run_sync(_null_async_fn)


async def test_prune_cache():
    # take worker's number and kill it for the next test
    pid1 = await run_sync(os.getpid)
    worker = DEFAULT_CONTEXT.worker_cache.pop()
    worker.kill()
    with trio.fail_after(1):
        await worker.wait()
    pid2 = await run_sync(os.getpid)
    # put dead worker into the cache (normal code never does this)
    DEFAULT_CONTEXT.worker_cache.appendleft(worker)
    pid2 = await run_sync(os.getpid)
    assert len(DEFAULT_CONTEXT.worker_cache) == 1
    assert pid1 != pid2


async def test_run_sync_large_job():
    n = 2 ** 20
    x = await run_sync(bytes, bytearray(n))
    assert len(x) == n


async def test_cache_scope():
    pid1 = await run_sync(os.getpid)
    async with cache_scope():
        pid2 = await run_sync(os.getpid)
        assert pid1 != pid2
    pid5 = await run_sync(os.getpid)
    assert pid5 == pid1


_NUM_RUNS = 0


def _retire_run_twice():
    global _NUM_RUNS
    if _NUM_RUNS >= 2:
        return True
    else:
        _NUM_RUNS += 1
        return False


async def test_cache_retire():
    async with cache_scope(retire=_retire_run_twice):
        pid2 = await run_sync(os.getpid)
        pid3 = await run_sync(os.getpid)
        assert pid3 == pid2
        pid4 = await run_sync(os.getpid)
        assert pid4 != pid2


async def test_cache_timeout():
    with trio.fail_after(20):
        async with cache_scope(idle_timeout=0):
            pid0 = await run_sync(os.getpid)
            while pid0 == await run_sync(os.getpid):
                pass  # pragma: no cover, rare race will reuse worker once or twice
    with trio.fail_after(20):
        async with cache_scope(idle_timeout=None):
            pid0 = await run_sync(os.getpid)
            assert pid0 == await run_sync(os.getpid)


@pytest.mark.parametrize("method", list(WorkerType))
async def test_cache_type(method):
    async with cache_scope(worker_type=method):
        assert 0 == await run_sync(int)


async def test_erroneous_scope_inputs():
    with pytest.raises(ValueError):
        async with cache_scope(idle_timeout=-1):
            pass
    with pytest.raises(ValueError):
        async with cache_scope(retire=0):
            pass
    with pytest.raises(ValueError):
        async with cache_scope(worker_type="wrong"):
            pass


def _bad_retire_fn():
    assert False


async def test_bad_retire_fn(capfd):
    with trio.fail_after(10):
        async with cache_scope(retire=_bad_retire_fn):
            with pytest.raises(BrokenWorkerError):
                await run_sync(os.getpid, cancellable=True)
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


def _delayed_bad_retire_fn():
    if _retire_run_twice():
        _bad_retire_fn()


async def test_delayed_bad_retire_fn(capfd):
    with trio.fail_after(10):
        cs = cache_scope(retire=_delayed_bad_retire_fn)
        await cs.__aenter__()
        try:
            await run_sync(bool, cancellable=True)
            await run_sync(bool, cancellable=True)
        finally:
            with pytest.raises(BrokenWorkerError):
                await cs.__aexit__(None, None, None)
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


def _loopy_retire_fn():  # pragma: no cover, will be killed
    if _retire_run_twice():
        import time

        while True:
            time.sleep(1)


async def test_loopy_retire_fn(manager):
    b = manager.Barrier(3)
    with trio.fail_after(20):
        cs = cache_scope(retire=_loopy_retire_fn)
        await cs.__aenter__()
        try:
            # open an extra worker to increase branch coverage in cache shutdown()
            async with trio.open_nursery() as n:
                n.start_soon(run_sync, b.wait)
                n.start_soon(run_sync, b.wait)
                await trio.to_thread.run_sync(b.wait)
            await run_sync(bool, cancellable=True)
        finally:
            with pytest.raises(BrokenWorkerError):
                await cs.__aexit__(None, None, None)


async def test_truthy_retire_fn_can_be_cancelled():
    with trio.move_on_after(0.1) as cs:
        async with cache_scope(retire=object):
            assert await run_sync(int)

    assert cs.cancelled_caught


def _atexit_shutdown():  # pragma: no cover, source code extracted
    # run in a subprocess, no access to globals
    import trio

    # note the order here: if trio_parallel is imported after multiprocessing,
    # specifically after invoking the logger, a more naive installation of the atexit
    # handler could be done and still pass the test
    import trio_parallel
    import multiprocessing  # noqa: F811

    # we inspect the logger output in stderr to validate the test
    multiprocessing.log_to_stderr(10)
    trio.run(trio_parallel.run_sync, bool)


_main_incantation = """
if __name__ == '__main__':
    {}()

"""

# trying to cover this leads to bugs like
# https://github.com/pytest-dev/pytest-cov/issues/243
# and a weird duplication of the statistics in reports.
# We don't even need coverage if it passes...
@pytest.mark.no_cover
def test_we_control_atexit_shutdowns(pytester, capfd):  # pragma: no cover
    # multiprocessing will either terminate or workers or lock up during its atexit
    # our graceful shutdown code allows atexit handlers *in the workers* to run as
    # well as avoiding being joined by the multiprocessing code. We test the latter.
    test_code = inspect.getsource(_atexit_shutdown)
    test_code += _main_incantation.format(_atexit_shutdown.__name__)
    result = pytester.run(sys.executable, "-c", test_code, timeout=10)
    assert result.ret == 0
    stderr = str(result.stderr)
    assert "[INFO/MainProcess] process shutting down" in stderr
    assert "calling join() for" not in stderr
    capfd.readouterr()
