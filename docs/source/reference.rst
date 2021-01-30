.. _worker_processes:

Worker Processes
----------------

Given that Python (and CPython in particular) has ongoing difficulties with
CPU-bound work, this package provides a method to dispatch synchronous function execution
to special subprocesses known as "Worker Processes". By default, it will create as many
workers as the system has CPUs (as reported by :func:`os.cpu_count`), allowing fair
and truly parallel dispatch of CPU-bound work. As with Trio threads, these processes
are cached to minimize latency and resource usage. Despite this,
executing a function in a process is at best an order of magnitude slower than in
a thread, and possibly even slower when dealing with large arguments or a cold cache.
Therefore, we recommend avoiding worker process dispatch for functions with a
duration of less than about 10 ms.

Unlike threads, subprocesses are strongly isolated from the parent process, which
allows two important features that cannot be portably implemented in threads:

  - Forceful cancellation: a deadlocked call or infinite loop can be cancelled
    by completely terminating the process.
  - Protection from errors: if a call segfaults or an extension module has an
    unrecoverable error, the worker may die but Trio will raise
    :exc:`BrokenWorkerError` and carry on.

In both cases the workers die suddenly and violently, and at an unpredictable point
in the execution of the dispatched function, so avoid using the cancellation feature
if loss of intermediate results, writes to the filesystem, or shared memory writes
may leave the larger system in an incoherent state.

.. module:: trio.to_process
.. currentmodule:: trio

Putting CPU-bound functions in worker processes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: trio.to_process.run_sync

.. autofunction:: trio.to_process.current_default_process_limiter

Exceptions and warnings
-----------------------

.. autoexception:: BrokenWorkerError
