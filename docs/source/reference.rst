Reference
=========

.. currentmodule:: trio_parallel

This project's aim is to use the lightest-weight, lowest-overhead, lowest latency
method to achieve parallelism of arbitrary Python code, and make it natively async for Trio.
Given that Python (and CPython in particular) has ongoing difficulties parallelizing
CPU-bound work in threads, this package dispatches synchronous function execution to
*subprocesses*. However, this project is not fundamentally constrained by that,
and will be considering subinterpreters, or any other avenue as they become available.

Running CPU-bound functions in parallel
---------------------------------------

The main interface for ``trio-parallel`` is :func:`run_sync`:

.. autofunction:: run_sync

.. note::

    :func:`trio_parallel.run_sync` does not work with functions defined at the REPL
    or in a Jupyter notebook cell due to the use of the `multiprocessing` ``spawn``
    context... *unless* cloudpickle_ is also installed!

A minimal program that dispatches work with :func:`run_sync` looks like this:

.. literalinclude:: examples/minimal.py

Just like that, you've dispatched a CPU-bound synchronous function to a worker
subprocess and returned the result! However, only doing this much is a bit pointless;
we are just expending the startup time of a whole python process to achieve the same
result that we could have gotten synchronously. To take advantage, some other task
needs to be able to run concurrently:

.. literalinclude:: examples/checkpointing.py

The output of this script indicates that the Trio event loop is running smoothly.
Still, this doesn't demonstrate much advantage over :func:`trio.to_thread.run_sync`.
You can see for yourself by substituting the function calls, since the call
signatures are intentionally identical.

No, ``trio-parallel`` really shines when your function has significant CPU-intensive
work that regularly involves the python interpreter:

.. literalinclude:: examples/parallel_loops.py

This script should output a roughly equal number of loops completed for each process,
as opposed to the lower and unbalanced number you might observe using threads.

As with Trio threads, these processes are cached to minimize latency and resource
usage. Despite this, executing a function in a process can take orders of magnitude
longer than in a thread when dealing with large arguments or a cold cache.

.. literalinclude:: examples/cache_warmup.py

Therefore, we recommend avoiding worker process dispatch
for synchronous functions with an expected duration of less than about 1 ms.

Controlling Concurrency
-----------------------

By default, ``trio-parallel`` will cache as many workers as the system has CPUs
(as reported by :func:`os.cpu_count`), allowing fair, maximal, truly-parallel
dispatch of CPU-bound work in the vast majority of cases. There are two ways to modify
this behavior. The first is the ``limiter`` argument of :func:`run_sync`, which
permits you to limit the concurrency of a specific function dispatch. In some cases,
it may be useful to modify the default limiter, which will affect all :func:`run_sync`
calls.

.. autofunction:: current_default_worker_limiter

Cancellation and Exceptions
---------------------------

Unlike threads, subprocesses are strongly isolated from the parent process, which
allows two important features that cannot be portably implemented in threads:

  - Forceful cancellation: a deadlocked call or infinite loop can be cancelled
    by completely terminating the process.
  - Protection from errors: if a call segfaults or an extension module has an
    unrecoverable error, the worker may die but the main process will raise
    a normal Python exception.

Cancellation
~~~~~~~~~~~~

Cancellation of :func:`trio_parallel.run_sync` is modeled after
:func:`trio.to_thread.run_sync`, with a ``cancellable`` keyword argument that
defaults to ``False``. Entry is an unconditional checkpoint, i.e. regardless of
the value of ``cancellable``. The only difference in behavior comes upon cancellation
when ``cancellable=True``. A Trio thread will be abandoned to run in the background
while this package will kill the worker with ``SIGKILL``/``TerminateProcess``:

.. literalinclude:: examples/cancellation.py

We recommend to avoid using the cancellation feature
if loss of intermediate results, writes to the filesystem, or shared memory writes
may leave the larger system in an incoherent state.

Exceptions
~~~~~~~~~~

.. autoexception:: BrokenWorkerError

Configuring workers
-------------------

By default, :func:`trio_parallel.run_sync` draws workers from a global cache
that is shared across sequential and between concurrent :func:`trio.run()`
calls, with workers' lifetimes limited to the life of the main process. This
covers most use cases, but for the many edge cases, `open_worker_context()`
yields a `WorkerContext` object on which `WorkerContext.run_sync()` pulls workers
from an isolated cache with behavior specified by the class arguments. It is only
advised to use this if specific control over worker type, state, or
lifetime is required.

.. autofunction:: open_worker_context
   :async-with: ctx

.. autoclass:: WorkerContext()
   :members:

One typical use case for configuring workers is to set a policy for taking a worker
out of service. For this, use the ``retire`` argument. This example shows how to
build (trivial) stateless and stateful worker retirement policies.

.. literalinclude:: examples/single_use_workers.py

A more realistic use-case might examine the worker process's memory usage (e.g. with
`psutil <https://psutil.readthedocs.io/en/latest/>`_) and retire if usage is too high.

If you are retiring workers frequently, like in the single-use case, a large amount
of process startup overhead will be incurred with the default worker type. If your
platform supports it, an alternate `WorkerType` might cut that overhead down.

.. autoclass:: WorkerType()

Internal Esoterica
------------------

You probably won't use these... but create an issue if you do and need help!

.. autofunction:: atexit_shutdown_grace_period

.. autofunction:: default_context_statistics

.. _cloudpickle: https://github.com/cloudpipe/cloudpickle