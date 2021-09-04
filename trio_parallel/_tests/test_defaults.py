"""End-to-end integrated tests of default cache"""

import inspect
import os
import subprocess
import sys

import pytest
import trio
from .._impl import DEFAULT_CONTEXT, run_sync, default_shutdown_grace_period


@pytest.fixture
def shutdown_cache():
    yield
    DEFAULT_CONTEXT.worker_cache.shutdown(60)
    DEFAULT_CONTEXT.worker_cache.clear()


def _raise_pid():
    raise ValueError(os.getpid())


async def test_run_sync(shutdown_cache):
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


async def test_entry_cancellation(manager, shutdown_cache):
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


async def test_kill_cancellation(manager, shutdown_cache):
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


async def test_uncancellable_cancellation(manager, shutdown_cache):
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


def test_we_control_atexit_shutdowns():
    # multiprocessing will either terminate or workers or lock up during its atexit.
    # Our graceful shutdown code allows atexit handlers *in the workers* to run as
    # well as avoiding being joined by the multiprocessing code. We test the latter.
    test_code = f"""{inspect.getsource(_atexit_shutdown)}\nif __name__ == '__main__':
        {_atexit_shutdown.__name__}()"""
    result = subprocess.run(
        [sys.executable, "-c", test_code],
        stderr=subprocess.PIPE,
        check=True,
        timeout=10,
    )
    assert b"[INFO/MainProcess] process shutting down" in result.stderr
    assert b"calling join() for" not in result.stderr


def test_change_default_grace_period():
    orig = default_shutdown_grace_period()
    assert orig == default_shutdown_grace_period()
    for x in (0, None, orig):
        assert x == default_shutdown_grace_period(x)
        assert x == default_shutdown_grace_period()
        assert x == default_shutdown_grace_period(-3)

    with pytest.raises(TypeError):
        default_shutdown_grace_period("forever")
    assert x == default_shutdown_grace_period()
