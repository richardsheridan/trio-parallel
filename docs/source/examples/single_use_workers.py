import multiprocessing
import os

import trio
import trio_parallel


def worker(i):
    print(i, "hello from", os.getpid())


def after_single_use():
    return True


WORKER_HAS_BEEN_USED = False


def after_dual_use():
    global WORKER_HAS_BEEN_USED
    if WORKER_HAS_BEEN_USED:
        return True  # retire
    else:
        WORKER_HAS_BEEN_USED = True
        return False  # don't retire... YET


async def amain():
    trio_parallel.current_default_worker_limiter().total_tokens = 4

    print("single use worker behavior:")
    async with trio_parallel.cache_scope(retire=after_single_use):
        async with trio.open_nursery() as nursery:
            for i in range(40):
                nursery.start_soon(trio_parallel.run_sync, worker, i)

    print("dual use worker behavior:")
    async with trio_parallel.cache_scope(retire=after_dual_use):
        async with trio.open_nursery() as nursery:
            for i in range(40):
                nursery.start_soon(trio_parallel.run_sync, worker, i)

    print("default behavior:")
    async with trio.open_nursery() as nursery:
        for i in range(40):
            nursery.start_soon(trio_parallel.run_sync, worker, i)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    trio.run(amain)
