import trio, trio_parallel
from operator import add

async def parallel_add():
    return await trio_parallel.run_sync(add, 1, 2)

# Guard against our workers trying to recursively start workers on startup
if __name__ == '__main__':
    assert add(1, 2) == trio.run(parallel_add) == 3