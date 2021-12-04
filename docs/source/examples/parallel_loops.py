import trio, trio_parallel, time

def loop(i=0):
    deadline = time.perf_counter() + 1
    # Arbitrary CPU-bound work
    while time.perf_counter() < deadline:
        i += 1
    print("Loops completed:", i)

async def amain():
    async with trio.open_nursery() as nursery:
        for i in range(4):
            nursery.start_soon(trio_parallel.run_sync, loop)

if __name__ == "__main__":
    trio.run(amain)
