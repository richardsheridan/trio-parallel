""" Tests of public API with mocked-out workers ("collaboration" tests)"""

import os
import sys
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
        assert trio.lowlevel.currently_ki_protected()
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

    async def _aclose(self):
        assert trio.lowlevel.currently_ki_protected()
        await super()._aclose()


@pytest.fixture
async def mock_context(monkeypatch):
    monkeypatch.setattr(_impl, "WorkerContext", MockContext)
    ctx = MockContext._create()
    if sys.platform == "win32":
        token = _impl.DEFAULT_CONTEXT_RUNVAR.set(ctx)
        yield ctx
        _impl.DEFAULT_CONTEXT_RUNVAR.reset(token)
    else:
        monkeypatch.setattr(_impl, "DEFAULT_CONTEXT", ctx)
        yield ctx


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
        _, _, obsvd_deadline = await run_sync(bool, kill_on_cancel=True)
        assert obsvd_deadline == deadline


async def test_cache_scope_args(mock_context):
    async with _impl.open_worker_context(
        init=float, retire=int, idle_timeout=33
    ) as ctx:
        await ctx.run_sync(bool)
        worker = ctx._worker_cache.pop()
        assert not ctx._worker_cache
        assert worker.init is float
        assert worker.retire is int
        assert worker.idle_timeout == 33


def _idfn(val):
    k = next(iter(val))
    v = val[k]
    return f"{k}-{v}"


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(idle_timeout=[-1]),
        dict(init=0),
        dict(retire=None),
        dict(grace_period=None),
    ],
    ids=_idfn,
)
async def test_erroneous_scope_types(kwargs):
    with pytest.raises(TypeError):
        async with _impl.open_worker_context(**kwargs):
            pytest.fail("should be unreachable")


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(worker_type="wrong"),
        dict(grace_period=-1),
        dict(idle_timeout=-1),
    ],
    ids=_idfn,
)
async def test_erroneous_scope_values(kwargs):
    with pytest.raises(ValueError):
        async with _impl.open_worker_context(**kwargs):
            pytest.fail("should be unreachable")


async def test_worker_returning_none_can_be_cancelled():
    with trio.move_on_after(0.1) as cs:
        ctx = MockContext._create(retire=_special_none_making_retire)
        assert await ctx.run_sync(int)
    assert cs.cancelled_caught


def test_cannot_instantiate_WorkerContext():
    with pytest.raises(TypeError):
        _impl.WorkerContext()


async def _assert_worker_pid(pid, matches):
    comparison = pid == await run_sync(os.getpid)
    assert comparison == matches


async def test_cache_scope_overrides_run_sync():
    pid = await run_sync(os.getpid)

    async with _impl.cache_scope():
        await _assert_worker_pid(pid, False)


async def test_cache_scope_overrides_nursery_task():
    pid = await run_sync(os.getpid)

    async def check_both_sides_of_task_status_started(pid, task_status):
        await _assert_worker_pid(pid, True)
        task_status.started()
        await _assert_worker_pid(pid, True)

    async with trio.open_nursery() as nursery:
        async with _impl.cache_scope():
            nursery.start_soon(_assert_worker_pid, pid, True)

    async with trio.open_nursery() as nursery:
        async with _impl.cache_scope():
            await nursery.start(check_both_sides_of_task_status_started, pid)


async def test_cache_scope_follows_task_tree_discipline():
    shared_nursery: Optional[trio.Nursery] = None

    async def make_a_cache_scope_around_nursery(task_status):
        nonlocal shared_nursery
        async with _impl.cache_scope(), trio.open_nursery() as shared_nursery:
            await _assert_worker_pid(pid, False)
            task_status.started()
            await e.wait()
        await _assert_worker_pid(pid, True)

    async def assert_elsewhere_in_task_tree():
        await _assert_worker_pid(pid, False)
        e.set()

    pid = await run_sync(os.getpid)
    e = trio.Event()
    async with trio.open_nursery() as nursery:
        await nursery.start(make_a_cache_scope_around_nursery)
        # this line tests the main difference from contextvars vs treevars
        shared_nursery.start_soon(assert_elsewhere_in_task_tree)


async def test_cache_scope_overrides_nested():
    pid1 = await run_sync(os.getpid)
    async with _impl.cache_scope():
        pid2 = await run_sync(os.getpid)
        async with _impl.cache_scope():
            await _assert_worker_pid(pid1, False)
            await _assert_worker_pid(pid2, False)


async def test_cache_scope_doesnt_override_explicit_context():
    async with _impl.open_worker_context() as ctx:
        pid = await ctx.run_sync(os.getpid)
        async with _impl.cache_scope():
            assert pid == await ctx.run_sync(os.getpid)
