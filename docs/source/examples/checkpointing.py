import trio, trio_parallel, time

async def check_scheduling_latency():
    for _ in range(10):
        t0 = trio.current_time()
        await trio.lowlevel.checkpoint()
        print(trio.current_time() - t0)

async def amain():
    async with trio.open_nursery() as nursery:
        nursery.start_soon(check_scheduling_latency)
        await trio_parallel.run_sync(time.sleep, 1)

if __name__ == "__main__":
    trio.run(amain)
