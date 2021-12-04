import trio, trio_parallel, time

def hello_delayed_world():
    print("Hello")
    time.sleep(1.0)
    print("world!")

async def amain():
    # warm up thread/process caches
    await trio_parallel.run_sync(bool)
    await trio.to_thread.run_sync(bool)

    with trio.move_on_after(0.5):
        await trio_parallel.run_sync(hello_delayed_world, cancellable=True)

    with trio.move_on_after(0.5):
        await trio.to_thread.run_sync(hello_delayed_world, cancellable=True)

    # grace period for abandoned thread
    await trio.sleep(0.6)

if __name__ == '__main__':
    trio.run(amain)
