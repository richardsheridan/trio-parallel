""" Tests of internal worker process API ("contract" tests)

    These are specific to subprocesses and you wouldn't expect these to pass
    with thread or subinterpreter workers.
"""
import math

import trio
import pytest

from _trio_parallel_workers._funcs import (
    _lambda,
    _return_lambda,
    _raise_ki,
    _never_halts,
    _segfault_out_of_bounds_pointer,
    _no_trio,
)
from .._proc import WORKER_PROC_MAP
from .._abc import BrokenWorkerError


@pytest.fixture(params=list(WORKER_PROC_MAP.values()), ids=list(WORKER_PROC_MAP.keys()))
async def worker(request):
    worker = request.param[0](math.inf, bool, bool)
    await worker.start()
    yield worker
    with trio.move_on_after(10) as cs:
        worker.shutdown()
        await worker.wait()
    if cs.cancelled_caught:
        with trio.fail_after(1) as cs:  # pragma: no cover, leads to failure case
            worker.kill()
            await worker.wait()
            pytest.fail(
                "tests should be responsible for killing and waiting if "
                "they do not lead to a graceful shutdown state"
            )


async def test_run_sync_cancel_infinite_loop(worker, manager):
    ev = manager.Event()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(worker.run_sync, _never_halts, ev)
        await trio.to_thread.run_sync(ev.wait, abandon_on_cancel=True)
        nursery.cancel_scope.cancel()
    with trio.fail_after(1):
        assert await worker.wait() in (-15, -9)


async def test_run_sync_raises_on_kill(worker, manager):
    ev = manager.Event()
    await worker.run_sync(int)  # running start so actual test is less racy
    with pytest.raises(BrokenWorkerError) as exc_info, trio.fail_after(5):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(worker.run_sync, _never_halts, ev)
            await trio.to_thread.run_sync(ev.wait, abandon_on_cancel=True)
            worker.kill()  # also tests multiple calls to worker.kill
    exitcode = await worker.wait()
    assert exitcode in (-15, -9)
    assert exc_info.value.args[-1].exitcode == exitcode


async def test_run_sync_raises_on_segfault(worker, capfd):
    with pytest.raises(BrokenWorkerError) as excinfo:
        with trio.fail_after(20):
            assert (await worker.run_sync(_segfault_out_of_bounds_pointer)).unwrap()
    exitcode = await worker.wait()
    assert exitcode  # not sure if we expect a universal value, but not 0 or None
    assert excinfo.value.args[-1].exitcode == exitcode


# to test that cancellation does not ever leave a living process behind
# currently requires manually targeting all but last checkpoints


async def test_exhaustively_cancel_run_sync(worker, manager):
    # cancel at job send if we reuse the worker
    ev = manager.Event()
    await worker.run_sync(int)
    with trio.fail_after(1):
        with trio.move_on_after(0):
            await worker.run_sync(_never_halts, ev)
        assert await worker.wait() in (-15, -9)

    # cancel at result recv is tested elsewhere


async def test_ki_does_not_propagate(worker):
    (await worker.run_sync(_raise_ki)).unwrap()


@pytest.mark.parametrize("job", [_lambda, _return_lambda])
async def test_unpickleable(job, worker):
    from pickle import PicklingError

    with pytest.raises((PicklingError, AttributeError)):
        (await worker.run_sync(job)).unwrap()


async def test_no_trio_in_subproc(worker):
    if worker.mp_context._name == "fork":
        pytest.skip("Doesn't matter on ForkProcWorker")
    assert (await worker.run_sync(_no_trio)).unwrap()
