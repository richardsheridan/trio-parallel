API Reference
=============

.. currentmodule:: trio_parallel

Running CPU-bound functions in parallel
---------------------------------------

.. autofunction:: run_sync

.. autofunction:: current_default_worker_limiter

.. autofunction:: atexit_shutdown_grace_period

.. autofunction:: default_context_statistics

Configuring workers
-------------------

.. autofunction:: open_worker_context
   :async-with: ctx

.. autoclass:: WorkerContext()
   :members:

.. autoclass:: WorkerType()

Exceptions and warnings
-----------------------

.. autoexception:: BrokenWorkerError