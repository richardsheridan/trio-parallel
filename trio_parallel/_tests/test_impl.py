import multiprocessing
import os

import pytest
import trio

from .._impl import DEFAULT_CACHE, run_sync, cache_scope


@pytest.fixture(autouse=True)
def empty_proc_cache():
    trio.run(DEFAULT_CACHE.clear)


def _echo_and_pid(x):  # pragma: no cover
    return (x, os.getpid())


def _raise_pid():  # pragma: no cover
    raise ValueError(os.getpid())


async def test_run_sync():
    trio_pid = os.getpid()
    limiter = trio.CapacityLimiter(1)

    x, child_pid = await run_sync(_echo_and_pid, 1, limiter=limiter)
    assert x == 1
    assert child_pid != trio_pid

    with pytest.raises(ValueError) as excinfo:
        await run_sync(_raise_pid, limiter=limiter)

    assert excinfo.value.args[0] != trio_pid


def _block_proc_on_queue(q, ev, done_ev):  # pragma: no cover
    # Make the process block for a controlled amount of time
    ev.set()
    q.get()
    done_ev.set()


async def test_run_sync_cancellation(capfd):
    async def child(q, ev, done_ev, cancellable):
        print("start")
        try:
            return await run_sync(
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
            await trio.testing.wait_all_tasks_blocked(0.01)
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
        await trio.testing.wait_all_tasks_blocked(0.01)
        assert capfd.readouterr().out.rstrip() == "start"
        await trio.to_thread.run_sync(ev.wait, cancellable=True)
        # Then cancel it.
        nursery.cancel_scope.cancel()
    # The task exited, but the process died
    assert not done_ev.is_set()
    assert capfd.readouterr().out.rstrip() == "exit"


async def _null_async_fn():  # pragma: no cover
    pass


async def test_run_sync_coroutine_error():
    with pytest.raises(TypeError, match="expected a sync function"):
        await run_sync(_null_async_fn)


async def test_prune_cache():
    # take proc's number and kill it for the next test
    while True:
        _, pid1 = await run_sync(_echo_and_pid, None)
        try:
            proc = DEFAULT_CACHE.pop()
        except IndexError:  # pragma: no cover
            # In CI apparently the worker occasionally doesn't make it all the way
            # to the barrier in time. This is only a slight inefficiency rather
            # than a bug so for now just work around it with this loop.
            continue
        else:
            break
    proc.kill()
    with trio.fail_after(1):
        await proc.wait()
    # put dead proc into the cache (normal code never does this)
    DEFAULT_CACHE.push(proc)
    # dead procs shouldn't pop out
    with pytest.raises(IndexError):
        DEFAULT_CACHE.pop()
    DEFAULT_CACHE.push(proc)
    # should spawn a new worker and remove the dead one
    _, pid2 = await run_sync(_echo_and_pid, None)
    assert len(DEFAULT_CACHE) == 1
    assert pid1 != pid2


async def test_run_sync_large_job():
    n = 2 ** 20
    x, _ = await run_sync(_echo_and_pid, bytearray(n))
    assert len(x) == n


async def test_cache_scope():
    _, pid1 = await run_sync(_echo_and_pid, None)
    async with cache_scope(mp_context="spawn", max_jobs=2):
        _, pid2 = await run_sync(_echo_and_pid, None)
        assert pid1 != pid2
        _, pid3 = await run_sync(_echo_and_pid, None)
        assert pid3 == pid2
        _, pid4 = await run_sync(_echo_and_pid, None)
        assert pid4 != pid2
    _, pid5 = await run_sync(_echo_and_pid, None)
    assert pid5 == pid1
    async with cache_scope(idle_timeout=0):
        _, pid6 = await run_sync(_echo_and_pid, None)
        await trio.sleep(0.01)
        _, pid7 = await run_sync(_echo_and_pid, None)
        assert pid6 != pid7
