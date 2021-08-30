import pytest
import trio

from .._impl import WORKER_MAP


@pytest.fixture(params=list(WORKER_MAP.values()), ids=list(WORKER_MAP.keys()))
async def worker(request):
    worker = request.param[0](None, bool)
    try:
        yield worker
    finally:
        with trio.move_on_after(2) as cs:
            worker.shutdown()
            await worker.wait()
        if cs.cancelled_caught:  # pragma: no cover, leads to failure case
            pytest.fail(
                "tests should be responsible for killing and waiting if they do not lead to "
                "a graceful shutdown state"
            )


async def test_run_sync(worker):
    assert not (await worker.run_sync(bool)).unwrap()


async def test_run_sync_large_job(worker):
    n = 2 ** 20
    x = (await worker.run_sync(bytes, bytearray(n))).unwrap()
    assert len(x) == n


async def _null_async_fn():  # pragma: no cover, coroutine called but not run
    pass


async def test_run_sync_coroutine_error(worker):
    with pytest.raises(TypeError, match="expected a sync function"):
        (await worker.run_sync(_null_async_fn)).unwrap()
