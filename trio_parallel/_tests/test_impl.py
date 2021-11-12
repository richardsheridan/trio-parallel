""" Tests of public API with mocked-out workers ("collaboration" tests)"""
import warnings
from typing import Callable, Optional

import pytest
import trio
from outcome import Outcome, capture

from .. import _impl
from .._abc import AbstractWorker, WorkerCache
from .._impl import (
    run_sync,
)


def _special_none_making_retire():  # pragma: no cover, never called
    pass


class MockWorker(AbstractWorker):
    def __init__(self, idle_timeout, init, retire):
        self.idle_timeout = idle_timeout
        self.init = init
        self.retire = retire

    async def start(self):
        await trio.lowlevel.checkpoint()

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
        while self:
            worker = self.popleft()
            if worker.retire is not _special_none_making_retire:
                self.appendleft(worker)
                return

    def shutdown(self, grace_period):
        for worker in self:
            worker.shutdown()
        self.shutdown_count += 1


class MockContext(_impl.WorkerContext):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.__dict__["_worker_class"] = MockWorker
        self.__dict__["_worker_cache"] = MockCache()


@pytest.fixture
def mock_context(monkeypatch):
    monkeypatch.setattr(_impl, "WorkerContext", MockContext)
    ctx = MockContext._create()
    monkeypatch.setattr(_impl, "DEFAULT_CONTEXT", ctx)
    return ctx


async def test_context_methods(mock_context):
    await run_sync(bool)
    await run_sync(bool)
    assert mock_context._worker_cache.pruned_count == 2
    assert mock_context._worker_cache.shutdown_count == 0
    await run_sync(bool)
    with trio.CancelScope() as cs:
        cs.cancel()
        await run_sync(bool)
    assert cs.cancelled_caught
    assert mock_context._worker_cache.pruned_count == 3
    assert mock_context._worker_cache.shutdown_count == 0


async def test_context_methods2(mock_context):
    async with _impl.open_worker_context() as ctx:
        s = ctx.statistics()
        assert s.idle_workers == 0
        assert s.running_workers == 0
        await ctx.run_sync(bool)
        s = ctx.statistics()
        assert s.idle_workers == 1
        assert s.running_workers == 0
        assert ctx._worker_cache.pruned_count == 3
    assert ctx._worker_cache.shutdown_count == 1
    s = ctx.statistics()
    assert s.idle_workers == 0
    assert s.running_workers == 0
    assert ctx._worker_cache.pruned_count == 4


async def test_cancellable(mock_context):
    deadline = trio.current_time() + 3
    with trio.CancelScope(deadline=deadline):
        _, _, obsvd_deadline = await run_sync(bool)
        assert obsvd_deadline == float("inf")
        _, _, obsvd_deadline = await run_sync(bool, cancellable=True)
        assert obsvd_deadline == deadline


async def test_cache_scope_args(mock_context):
    async with _impl.open_worker_context(
        init=float, retire=int, idle_timeout=33
    ) as ctx:
        await ctx.run_sync(bool)
        worker = ctx._worker_cache.pop()
        assert worker.init is float
        assert worker.retire is int
        assert worker.idle_timeout == 33


async def test_erroneous_scope_inputs():
    with pytest.raises(TypeError):
        async with _impl.open_worker_context(idle_timeout=[-1]):
            pytest.fail("should be unreachable")
    with pytest.raises(TypeError):
        async with _impl.open_worker_context(init=0):
            pytest.fail("should be unreachable")
    with pytest.raises(TypeError):
        async with _impl.open_worker_context(retire=None):
            pytest.fail("should be unreachable")
    with pytest.raises(TypeError):
        async with _impl.open_worker_context(grace_period=object()):
            pytest.fail("should be unreachable")
    with pytest.raises(ValueError):
        with warnings.catch_warnings():  # spurious DeprecationWarning on 3.7
            warnings.simplefilter("ignore")
            async with _impl.open_worker_context(worker_type="wrong"):
                pytest.fail("should be unreachable")
    with pytest.raises(ValueError):
        async with _impl.open_worker_context(grace_period=-1):
            pytest.fail("should be unreachable")
    with pytest.raises(ValueError):
        async with _impl.open_worker_context(idle_timeout=-1):
            pytest.fail("should be unreachable")


async def test_worker_returning_none_can_be_cancelled():
    with trio.move_on_after(0.1) as cs:
        ctx = MockContext._create(retire=_special_none_making_retire)
        assert await ctx.run_sync(int)
    assert cs.cancelled_caught


def test_cannot_instantiate_WorkerContext():
    with pytest.raises(TypeError):
        _impl.WorkerContext()
