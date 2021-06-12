import binascii
import multiprocessing
import time
import secrets

import trio
import trio_parallel


async def to_process_map_as_completed(
    sync_fn,
    job_aiter,
    cancellable=False,
    limiter=None,
    *,
    task_status,
):
    if limiter is None:
        limiter = trio_parallel.current_default_worker_limiter()
    send_chan, recv_chan = trio.open_memory_channel(0)
    task_status.started(recv_chan)

    async def worker(job_item, task_status):
        # Backpressure: hold limiter for entire task to avoid
        # spawning too many workers
        async with limiter:
            task_status.started()
            result = await trio_parallel.run_sync(
                sync_fn,
                *job_item,
                cancellable=cancellable,
                limiter=trio.CapacityLimiter(1),
            )
            await send_chan.send(result)

    async with send_chan, trio.open_nursery() as nursery:
        async for job_item in job_aiter:
            await nursery.start(worker, job_item)


async def data_generator(*, task_status, limiter=None):
    send_chan, recv_chan = trio.open_memory_channel(0)
    task_status.started(recv_chan)
    if limiter is None:
        limiter = trio_parallel.current_default_worker_limiter()
    async with send_chan:
        for j in range(100):
            # Just pretend this is coming from disk or network
            data = secrets.token_hex()
            # Inputs MUST be throttled with the SAME limiter as
            # the rest of the steps of the pipeline
            async with limiter:
                await send_chan.send((j, data))


def clean_data(j, data):
    time.sleep(secrets.randbelow(2) / 10)
    return j, data.replace("deadbeef", "f00dbeef")


def load_data(j, data):
    time.sleep(secrets.randbelow(2) / 10)
    return j, binascii.unhexlify(data)


def compute(j, data):
    time.sleep(secrets.randbelow(2) / 10)
    n = 0
    for value in data:
        if value % 2:
            n += 1
    return j, n


async def amain():
    i = 1
    t0 = trio.current_time()
    async with trio.open_nursery() as nursery:
        data_aiter = await nursery.start(data_generator)
        clean_data_aiter = await nursery.start(
            to_process_map_as_completed,
            clean_data,
            data_aiter,
        )
        loaded_data_aiter = await nursery.start(
            to_process_map_as_completed,
            load_data,
            clean_data_aiter,
        )
        computational_result_aiter = await nursery.start(
            to_process_map_as_completed,
            compute,
            loaded_data_aiter,
        )
        async for result in computational_result_aiter:
            print(i, (trio.current_time() - t0) / i, *result)
            if result[1] <= 9:
                print("Winner! after ", trio.current_time() - t0, "seconds")
                nursery.cancel_scope.cancel()
            i += 1
        print("No extra-even bytestrings after ", trio.current_time() - t0, "seconds")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    trio.run(amain)
