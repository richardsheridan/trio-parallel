import inspect
import os
import subprocess
import sys
from typing import Callable, Optional

import pytest
import trio
from outcome import Outcome, capture

from .. import _impl
from .._abc import AbstractWorker, WorkerCache
from .._impl import (
    DEFAULT_CONTEXT,
    WorkerType,
    run_sync,
    cache_scope,
    default_shutdown_grace_period,
)


# END TO END INTEGRATED TESTS OF DEFAULT CACHE


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
    # multiprocessing will either terminate or workers or lock up during its atexit
    # our graceful shutdown code allows atexit handlers *in the workers* to run as
    # well as avoiding being joined by the multiprocessing code. We test the latter.
    test_code = f"""{inspect.getsource(_atexit_shutdown)}\nif __name__ == '__main__':
        {_atexit_shutdown.__name__}()"""
    result = subprocess.run(
        [sys.executable, "-c", test_code],
        stderr=subprocess.PIPE,
        check=True,
        timeout=10,
    )
    assert result.returncode == 0
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


# API TESTS WITH MOCKED-OUT WORKERS ("collaboration" tests)


def _special_none_making_retire():  # pragma: no cover, never called
    pass


class MockWorker(AbstractWorker):
    def __init__(self, idle_timeout: float, retire: Optional[Callable[[], bool]]):
        self.idle_timeout = idle_timeout
        self.retire = retire

    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        await trio.lowlevel.checkpoint()
        if self.retire is not _special_none_making_retire:
            return capture(
                lambda *a: (sync_fn, args, trio.current_effective_deadline())
            )


class MockCache(WorkerCache):
    pruned_count = 0
    shutdown_count = 0

    def prune(self):
        self.pruned_count += 1

    def shutdown(self, grace_period):
        self.shutdown_count += 1


class MockContext(_impl.WorkerContext):
    active_contexts = list()

    def __init__(self, idle_timeout, retire, worker_class, worker_cache):
        super().__init__(idle_timeout, retire, worker_class, worker_cache)
        self.called_worker_class = worker_class
        self.called_retire = retire
        self.called_idle_timeout = idle_timeout
        self.worker_class = MockWorker
        self.worker_cache = MockCache()
        self.active_contexts.append(self)


@pytest.fixture
def mock_def_cache(monkeypatch):
    cache = MockCache()
    monkeypatch.setattr(DEFAULT_CONTEXT, "worker_cache", cache)
    monkeypatch.setattr(DEFAULT_CONTEXT, "worker_class", MockWorker)
    return cache


@pytest.fixture
def mock_context(monkeypatch):
    monkeypatch.setattr(_impl, "WorkerContext", MockContext)
    yield
    MockContext.active_contexts.clear()


async def test_cache_scope(mock_def_cache, mock_context):
    sync_fn, args, current_effective_deadline = await run_sync(bool)
    assert sync_fn is bool
    assert not args
    assert not MockContext.active_contexts
    async with cache_scope():
        sync_fn, args, current_effective_deadline = await run_sync(bool)
        assert sync_fn is bool
        assert not args
        assert current_effective_deadline > 0
        assert len(MockContext.active_contexts) == 1


async def test_cache_scope_methods(mock_context):
    async with cache_scope():
        await run_sync(bool)
        await run_sync(bool)
        active_context = MockContext.active_contexts.pop()
    assert active_context.worker_cache.pruned_count == 2
    assert active_context.worker_cache.shutdown_count == 1
    async with cache_scope():
        await run_sync(bool)
        with trio.CancelScope() as cs:
            cs.cancel()
            await run_sync(bool)
        assert cs.cancelled_caught
        active_context = MockContext.active_contexts.pop()
    assert active_context.worker_cache.pruned_count == 1
    assert active_context.worker_cache.shutdown_count == 1
    async with cache_scope():
        deadline = trio.current_time() + 3
        with trio.CancelScope(deadline=deadline):
            _, _, obsvd_deadline = await run_sync(bool)
            assert obsvd_deadline == float("inf")
            _, _, obsvd_deadline = await run_sync(bool, cancellable=True)
            assert obsvd_deadline == deadline
        await run_sync(bool)
        active_context = MockContext.active_contexts.pop()
    assert active_context.worker_cache.pruned_count == 3
    assert active_context.worker_cache.shutdown_count == 1


async def test_cache_scope_args(mock_context):
    async with cache_scope(retire=int, idle_timeout=33):
        active_context = MockContext.active_contexts.pop()
        assert active_context.called_retire is int
        assert active_context.called_idle_timeout == 33


async def test_cache_scope_nesting(mock_context):
    async with cache_scope():
        async with cache_scope():
            async with cache_scope():
                assert len(MockContext.active_contexts) == 3
                MockContext.active_contexts.pop()
            async with cache_scope():
                assert len(MockContext.active_contexts) == 3
                MockContext.active_contexts.pop()


@pytest.mark.parametrize("method", list(WorkerType))
async def test_cache_type(method, mock_context):
    async with cache_scope(worker_type=method):
        active_context = MockContext.active_contexts.pop()
        assert active_context.called_worker_class == _impl.WORKER_MAP[method][0]


async def test_erroneous_scope_inputs(mock_context):
    with pytest.raises(ValueError):
        async with cache_scope(idle_timeout=-1):
            assert False, "should be unreachable"
    assert not MockContext.active_contexts
    with pytest.raises(ValueError):
        async with cache_scope(retire=0):
            assert False, "should be unreachable"
    assert not MockContext.active_contexts
    with pytest.raises(ValueError):
        async with cache_scope(worker_type="wrong"):
            assert False, "should be unreachable"
    with pytest.raises(ValueError):
        async with cache_scope(grace_period=-2):
            assert False, "should be unreachable"
    assert not MockContext.active_contexts


async def test_worker_returning_none_can_be_cancelled(mock_context):
    with trio.move_on_after(0.1) as cs:
        async with cache_scope(retire=_special_none_making_retire):
            assert not await run_sync(int)
    assert cs.cancelled_caught
