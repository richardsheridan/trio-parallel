""" Tests of run_sync API with mocked-out workers ("collaboration" tests)"""
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
)


def _special_none_making_retire():  # pragma: no cover, never called
    pass


class MockWorker(AbstractWorker):
    def __init__(self, idle_timeout, init, retire):
        self.idle_timeout = idle_timeout
        self.retire = retire

    async def run_sync(self, sync_fn: Callable, *args) -> Optional[Outcome]:
        await trio.lowlevel.checkpoint()
        if self.retire is not _special_none_making_retire:
            return capture(
                lambda *a: (sync_fn, args, trio.current_effective_deadline())
            )

    def shutdown(self):
        self.retire = _special_none_making_retire

    async def wait(self):  # pragma: no cover, only here to satisfy ABC
        pass


class MockCache(WorkerCache):
    pruned_count = 0
    shutdown_count = 0

    def prune(self):
        self.pruned_count += 1

    def shutdown(self, grace_period):
        for worker in self:
            worker.shutdown()
        self.shutdown_count += 1


class MockContext(_impl.WorkerContext):
    active_contexts = list()

    def __init__(self, idle_timeout, init, retire, worker_class, worker_cache):
        super().__init__(idle_timeout, init, retire, worker_class, worker_cache)
        self.called_worker_class = worker_class
        self.called_init = init
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
    async with cache_scope(init=float, retire=int, idle_timeout=33):
        active_context = MockContext.active_contexts.pop()
        assert active_context.called_init is float
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
    with pytest.raises(TypeError):
        async with cache_scope(init=0):
            assert False, "should be unreachable"
    with pytest.raises(TypeError):
        async with cache_scope(retire=0):
            assert False, "should be unreachable"
    assert not MockContext.active_contexts
    with pytest.raises(TypeError):
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
