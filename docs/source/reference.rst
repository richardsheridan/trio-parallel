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

.. autoclass:: WorkerContext

   .. automethod:: run_sync

   .. automethod:: aclose

.. autoclass:: WorkerType

Exceptions and warnings
-----------------------

.. autoexception:: BrokenWorkerError