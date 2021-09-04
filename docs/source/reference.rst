API Reference
=============

.. currentmodule:: trio_parallel

Running CPU-bound functions in parallel
---------------------------------------

.. autofunction:: run_sync

.. autofunction:: current_default_worker_limiter

.. autofunction:: default_shutdown_grace_period

Configuring workers
-------------------

.. autofunction:: cache_scope

.. autoclass:: WorkerType

Exceptions and warnings
-----------------------

.. autoexception:: BrokenWorkerError