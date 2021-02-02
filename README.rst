trio-parallel
=============

Welcome to `trio-parallel <https://github.com/richardsheridan/trio-parallel>`__!

CPU parallelism for Trio

License: Your choice of MIT or Apache License 2.0

Do you have CPU bound work that just keeps slowing down your event loop no matter
what you try? Do you need to get all those cores humming at once?
This is the library for you!

.. code-block:: python

    import multiprocessing
    import trio
    import trio_parallel
    import time


    def hard_work(n, x):
        t = time.perf_counter() + n
        y = x
        while time.perf_counter() < t:
            x = not x
        print(y, "transformed into", x)
        return x

    async def too_slow():
        await trio_parallel.run_sync(hard_work, 20, False, cancellable=True)


    async def amain():
        t0 = time.perf_counter()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(trio_parallel.run_sync, hard_work, 3, True)
            nursery.start_soon(trio_parallel.run_sync, hard_work, 1, False)
            nursery.start_soon(too_slow)
            result = await trio_parallel.run_sync(hard_work, 2, None)
            nursery.cancel_scope.cancel()
        print("got", result, "in", time.perf_counter() - t0, "seconds")


    if __name__ == "__main__":
        multiprocessing.freeze_support()
        trio.run(amain)


Documentation
-------------
The full API is documented at `<https://trio-parallel.readthedocs.io/>`__

Features
--------

- Bypasses the GIL for CPU bound work
- Minimal API complexity (looks and feels like Trio threads)
- Cross-platform
- Automatic LIFO caching of subprocesses
- Cancel seriously misbehaving code

  - currently via SIGKILL/TerminateProcess

- Convert segfaults and other scary things to catchable errors

This project aims to use the lightest-weight, lowest-overhead, lowest latency
method to achieve CPU parallelism of arbitrary Python code. At the moment, that
means *subprocesses*. However, this project is not at all constrained by that,
and will be considering subinterpreters, or any other avenue as they become available.

Currently, this project is based on ``multiprocessing`` has all the usual multiprocessing caveats
(``freeze_support``, pickleable objects only). The case for basing these workers on
multiprocessing is that it keeps a lot of complexity outside of the project while
offering a set of quirks that users are likely already familiar with.

FAQ
---

Can I have my workers talk to each other?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is currently possible through the use of `multiprocessing.Manager`,
but we don't and will not support it. Instead, try using `trio.run_process` and
having the various Trio runs talk to each other over sockets. Also, look into
`tractor <https://github.com/goodboy/tractor>`_?

Can I let my workers outlive the main Trio process?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The worker processes are started with the `daemon` flag for lifetime management,
so this use case is not supported.

How should I map a function over a collection of arguments?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is fully possible but we leave the implementation of that up to you.
Also, look into `trimeter <https://github.com/python-trio/trimeter>`_?

Contributing
------------
If you notice any bugs, need any help, or want to contribute any code,
GitHub issues and pull requests are very welcome! Please read the
`code of conduct <CODE_OF_CONDUCT.md>`_.