.. documentation master file, created by
   sphinx-quickstart on Sat Jan 21 19:11:14 2017.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.


=======================================
trio-parallel: CPU parallelism for Trio
=======================================

.. image:: https://github.com/richardsheridan/trio-parallel/workflows/CI/badge.svg
  :target: https://github.com/richardsheridan/trio-parallel/
  :alt: CI status

.. image:: https://codecov.io/gh/richardsheridan/trio-parallel/branch/main/graph/badge.svg?token=EQqs2abxxG
  :target: https://codecov.io/gh/richardsheridan/trio-parallel
  :alt: Test coverage

.. image:: https://readthedocs.org/projects/trio-parallel/badge/
  :target: https://trio-parallel.readthedocs.io/
  :alt: Documentation

.. image:: https://badgen.net/badge/code%20style/black/black
  :target: https://github.com/psf/black
  :alt: Code style: black

.. image:: https://badgen.net/pypi/v/trio-parallel
  :target: https://pypi.org/project/trio-parallel/
  :alt: Latest Pypi version

.. image:: https://badgen.net/github/last-commit/richardsheridan/trio-parallel/main
  :target: https://github.com/richardsheridan/trio-parallel/
  :alt: last commit

License: Your choice of MIT or Apache License 2.0

Do you have CPU bound work that just keeps slowing down your event loop no matter
what you try? Do you need to get all those cores humming at once?
This is the library for you!

Given that Python (and CPython in particular) has ongoing difficulties with
CPU-bound work, this package provides :func:`trio_parallel.run_sync` to dispatch
synchronous function execution to special subprocesses known as "Worker Processes".
By default, it will create as many workers as the system has CPUs
(as reported by :func:`os.cpu_count`), allowing fair
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
    unrecoverable error, the worker may die but :func:`trio_parallel.run_sync` will raise
    :exc:`trio_parallel.BrokenWorkerError` and carry on.

In both cases the workers die suddenly and violently, and at an unpredictable point
in the execution of the dispatched function, so avoid using the cancellation feature
if loss of intermediate results, writes to the filesystem, or shared memory writes
may leave the larger system in an incoherent state.

.. currentmodule:: trio_parallel

Running CPU-bound functions parallel
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: run_sync

.. autofunction:: current_default_process_limiter

Exceptions and warnings
-----------------------

.. autoexception:: BrokenWorkerError

============
 Navigation
============

.. toctree::
   :maxdepth: 2

   history.rst

====================
 Indices and tables
====================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
* :ref:`glossary`
