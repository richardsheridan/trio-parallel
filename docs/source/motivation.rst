Motivation
-----------

This project's aim is to use the lightest-weight, lowest-overhead, lowest latency
method to achieve parallelism of arbitrary Python code, and make it natively async for Trio.
Given that Python (and CPython in particular) has ongoing difficulties parallelizing
CPU-bound work in threads, this package provides :func:`trio_parallel.run_sync` to dispatch
synchronous function execution to *subprocesses*. However, this project is not at all constrained by that,
and will be considering subinterpreters, or any other avenue as they become available.

By default, it will create as many workers as the system has CPUs
(as reported by :func:`os.cpu_count`), allowing fair
and truly parallel dispatch of CPU-bound work. As with Trio threads, these processes
are cached to minimize latency and resource usage. Despite this,
executing a function in a process is at best an order of magnitude slower than in
a thread, and possibly even slower when dealing with large arguments or a cold cache.
Therefore, we recommend avoiding worker process dispatch for functions with a
duration of less than about 1 ms.

Unlike threads, subprocesses are strongly isolated from the parent process, which
allows two important features that cannot be portably implemented in threads:

  - Forceful cancellation: a deadlocked call or infinite loop can be cancelled
    by completely terminating the process.
  - Protection from errors: if a call segfaults or an extension module has an
    unrecoverable error, the worker may die but :func:`trio_parallel.run_sync` will raise
    :exc:`trio_parallel.BrokenWorkerError` and carry on.

In both cases the workers die suddenly and violently, and at an unpredictable point
in the execution of the dispatched function, so avoid using the cancellation feature
if loss of intermediate results, writes to the filesystem, or shared memory writes
may leave the larger system in an incoherent state.