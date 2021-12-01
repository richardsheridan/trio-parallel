import trio, trio_parallel

async def amain():
    t0 = trio.current_time()
    await trio_parallel.run_sync(bool)
    t1 = trio.current_time()
    await trio_parallel.run_sync(bool)
    t2 = trio.current_time()
    await trio_parallel.run_sync(bytearray, 10**8)
    t3 = trio.current_time()
    print("Cold cache latency:", t1-t0)
    print("Warm cache latency:", t2-t1)
    print("IPC latency/MB:", (t3-t2)/10**2)

if __name__ == '__main__':
    trio.run(amain)
