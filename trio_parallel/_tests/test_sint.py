import pytest

from _trio_parallel_workers._funcs import (
    _lambda,
    _return_lambda,
    _no_trio,
)


@pytest.mark.parametrize("job", [_lambda, _return_lambda])
async def test_unpickleable(job, worker):
    from pickle import PicklingError

    with pytest.raises((PicklingError, AttributeError)):
        (await worker.run_sync(job)).unwrap()


async def test_no_trio_in_subproc(worker):
    if worker.mp_context._name == "fork":
        pytest.skip("Doesn't matter on ForkProcWorker")
    assert (await worker.run_sync(_no_trio)).unwrap()
