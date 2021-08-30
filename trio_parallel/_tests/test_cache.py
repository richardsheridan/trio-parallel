import pytest
import trio

from .._impl import WORKER_MAP
from .._abc import BrokenWorkerError


@pytest.fixture(params=list(WORKER_MAP.values()), ids=list(WORKER_MAP.keys()))
async def cache_and_workertype(request):
    worker_type, cache_type = request.param
    cache = cache_type()
    try:
        yield cache, worker_type
    finally:
        await cache.shutdown()  # internal assertion of clean shutdown


async def test_prune_cache(cache_and_workertype):
    # setup phase
    cache, worker_type = cache_and_workertype
    dead_worker = worker_type(0, bool)
    assert not (await dead_worker.run_sync(bool)).unwrap()
    with trio.fail_after(1):
        await dead_worker.wait()
    live_worker = worker_type(None, bool)
    assert not (await live_worker.run_sync(bool)).unwrap()
    # put dead worker into the cache on the left
    cache.extend(iter([dead_worker, live_worker]))
    cache.prune()
    assert live_worker in cache
    assert dead_worker not in cache


_NUM_RUNS = 0


def _retire_run_twice():
    global _NUM_RUNS
    if _NUM_RUNS >= 2:
        return True
    else:
        _NUM_RUNS += 1
        return False


async def test_retire(cache_and_workertype):
    cache, worker_type = cache_and_workertype
    worker = worker_type(None, _retire_run_twice)
    try:
        assert await worker.run_sync(bool) is not None
        assert await worker.run_sync(bool) is not None
        assert await worker.run_sync(bool) is None
    finally:
        with trio.fail_after(1):
            assert await worker.wait() == 0


def _bad_retire_fn():
    assert False


async def test_bad_retire_fn(cache_and_workertype, capfd):
    cache, worker_type = cache_and_workertype
    worker = worker_type(None, _bad_retire_fn)
    with pytest.raises(BrokenWorkerError):
        await worker.run_sync(bool)
    with trio.fail_after(1):
        assert await worker.wait() == 1
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


def _delayed_bad_retire_fn():
    if _retire_run_twice():
        _bad_retire_fn()


async def test_delayed_bad_retire_fn(cache_and_workertype, capfd):
    cache, worker_type = cache_and_workertype
    worker = worker_type(None, _delayed_bad_retire_fn)
    await worker.run_sync(bool)
    await worker.run_sync(bool)
    with pytest.raises(BrokenWorkerError):
        await worker.run_sync(bool)
    with trio.fail_after(1):
        assert await worker.wait() == 1
    out, err = capfd.readouterr()
    assert "trio-parallel worker process" in err
    assert "AssertionError" in err


def _loopy_retire_fn():  # pragma: no cover, will be killed
    if _retire_run_twice():
        import time

        while True:
            time.sleep(1)


async def test_loopy_retire_fn(cache_and_workertype):
    cache, worker_type = cache_and_workertype
    worker = worker_type(None, _loopy_retire_fn)
    await worker.run_sync(bool)
    await worker.run_sync(bool)
    cache.append(worker)

    with pytest.raises(BrokenWorkerError):
        await cache.shutdown()
    cache.clear()