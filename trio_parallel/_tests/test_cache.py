""" Tests of internal cache API ("contract" tests)"""
import math

import pytest
import trio

from ._funcs import (
    _init_run_twice,
    _retire_run_twice,
    _bad_retire_fn,
    _delayed_bad_retire_fn,
    _loopy_retire_fn,
    _monkeypatch_max_timeout,
)
from .._impl import WORKER_MAP
from .._abc import BrokenWorkerError


@pytest.fixture(params=list(WORKER_MAP.values()), ids=list(WORKER_MAP.keys()))
def cache_and_workertype(request):
    worker_type, cache_type = request.param
    cache = cache_type()
    try:
        yield cache, worker_type
    finally:
        cache.shutdown(10)  # internal assertion of clean shutdown


async def test_prune_cache(cache_and_workertype):
    # setup phase
    cache, worker_type = cache_and_workertype
    dead_worker = worker_type(0.3, bool, bool)
    await dead_worker.start()
    assert (await dead_worker.run_sync(_monkeypatch_max_timeout)).unwrap() is True
    with trio.fail_after(2):
        assert await dead_worker.wait() is not None
    live_worker = worker_type(math.inf, bool, bool)
    await live_worker.start()
    assert (await live_worker.run_sync(bool)).unwrap() is False
    # put dead worker into the cache on the left
    cache.extend(iter([dead_worker, live_worker]))
    cache.prune()
    assert live_worker in cache
    assert dead_worker not in cache


async def test_retire(cache_and_workertype):
    cache, worker_type = cache_and_workertype
    worker = worker_type(math.inf, _init_run_twice, _retire_run_twice)
    await worker.start()
    try:
        assert await worker.run_sync(bool) is not None
        assert await worker.run_sync(bool) is not None
        assert await worker.run_sync(bool) is None
    finally:
        with trio.fail_after(1):
            assert await worker.wait() == 0


async def test_bad_retire_fn(cache_and_workertype, capfd):
    cache, worker_type = cache_and_workertype
    if worker_type.mp_context._name == "forkserver":
        pytest.skip("capfd doesn't work on ForkserverProcWorker")
    worker = worker_type(math.inf, bool, _bad_retire_fn)
    await worker.start()
    await worker.run_sync(bool)
    with pytest.raises(BrokenWorkerError):
        await worker.run_sync(bool)
    with trio.fail_after(1):
        assert await worker.wait() == 1
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


async def test_delayed_bad_retire_fn(cache_and_workertype, capfd):
    cache, worker_type = cache_and_workertype
    if worker_type.mp_context._name == "forkserver":
        pytest.skip("capfd doesn't work on ForkserverProcWorker")
    worker = worker_type(math.inf, _init_run_twice, _delayed_bad_retire_fn)
    await worker.start()
    await worker.run_sync(bool)
    await worker.run_sync(bool)
    with pytest.raises(BrokenWorkerError):
        await worker.run_sync(bool)
    with trio.fail_after(1):
        assert await worker.wait() == 1

    cache.append(worker)
    with pytest.raises(BrokenWorkerError):
        cache.shutdown(0.5)
    cache.clear()
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


async def test_loopy_retire_fn(cache_and_workertype, monkeypatch):
    from .. import _abc

    cache, worker_type = cache_and_workertype
    worker = worker_type(math.inf, _init_run_twice, _loopy_retire_fn)
    await worker.start()
    await worker.run_sync(bool)
    await worker.run_sync(bool)

    # increase coverage in cache.shutdown
    monkeypatch.setattr(_abc, "MAX_TIMEOUT", 0.1)
    cache.append(worker)
    with pytest.raises(BrokenWorkerError):
        cache.shutdown(0.5)
    cache.clear()


async def test_shutdown(cache_and_workertype):
    cache, worker_type = cache_and_workertype
    # test that shutdown actually works
    worker = worker_type(math.inf, bool, bool)
    await worker.start()
    await worker.run_sync(bool)
    cache.append(worker)
    cache.shutdown(1)
    worker = cache.pop()
    with trio.fail_after(1):
        assert await worker.wait() is not None
    # test that math.inf is a valid input
    # contained in same test with above because we want to first
    # assert that shutdown works at all!
    worker = worker_type(math.inf, bool, bool)
    await worker.start()
    await worker.run_sync(bool)
    cache.append(worker)
    cache.shutdown(math.inf)
    cache.clear()


async def test_shutdown_immediately(cache_and_workertype):
    cache, worker_type = cache_and_workertype
    worker = worker_type(math.inf, bool, bool)
    await worker.start()
    await worker.run_sync(bool)
    cache.append(worker)
    with pytest.raises(BrokenWorkerError):
        cache.shutdown(0)
    cache.clear()
