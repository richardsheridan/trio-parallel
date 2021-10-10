""" Tests of internal worker API ("contract" tests)

    All workers should pass these tests, regardless of implementation
"""
import math

import pytest
import trio

from ._funcs import _null_async_fn
from .._impl import WORKER_MAP


@pytest.fixture(params=list(WORKER_MAP.values()), ids=list(WORKER_MAP.keys()))
async def worker(request):
    worker = request.param[0](math.inf, bool, bool)
    await worker.start()
    try:
        yield worker
    finally:
        with trio.move_on_after(2) as cs:
            worker.shutdown()
            await worker.wait()
        if cs.cancelled_caught:  # pragma: no cover, leads to failure case
            pytest.fail(
                "tests should be responsible for killing and waiting if they do not "
                "lead to a graceful shutdown state"
            )


async def test_cancel_start(worker):
    # awkwardly we don't want the started worker provided by the fixture
    worker = type(worker)(math.inf, bool, bool)
    # cancel at startup
    with trio.fail_after(1):
        with trio.move_on_after(0) as cs:
            await worker.start()
        assert cs.cancelled_caught
        assert await worker.wait() is None


async def test_run_sync(worker):
    assert (await worker.run_sync(bool)).unwrap() is False


async def test_run_sync_large_job(worker):
    n = 2 ** 20
    x = (await worker.run_sync(bytes, bytearray(n))).unwrap()
    assert len(x) == n


async def test_run_sync_coroutine_error(worker):
    with pytest.raises(TypeError, match="expected a sync function"):
        (await worker.run_sync(_null_async_fn)).unwrap()


async def test_clean_exit_on_shutdown(worker, capfd):
    if worker.mp_context._name == "forkserver":
        pytest.skip("capfd doesn't work on ForkserverProcWorker")
    # This could happen on weird __del__/weakref/atexit situations.
    # It was not visible on normal, clean exits because multiprocessing
    # would call terminate before pipes were GC'd.
    assert (await worker.run_sync(bool)).unwrap() is False
    worker.shutdown()
    with trio.fail_after(1):
        assert await worker.wait() == 0
    out, err = capfd.readouterr()
    assert not out
    assert not err
