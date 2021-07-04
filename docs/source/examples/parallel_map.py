import multiprocessing
import random

import trio
import trio_parallel


def twiddle(i):
    for j in range(50000):
        i *= random.choice((-1, 1))
    return i


async def parallel_map(fn, inputs, *args):
    results = [None] * len(inputs)

    async def worker(j, inp):
        results[j] = await trio_parallel.run_sync(fn, inp, *args)
        print(j, "done")

    async with trio.open_nursery() as nursery:
        for i, inp in enumerate(inputs):
            nursery.start_soon(worker, i, inp)

    return results


if __name__ == "__main__":
    multiprocessing.freeze_support()
    print(trio.run(parallel_map, twiddle, range(100)))
