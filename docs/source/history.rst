Release history
===============

.. currentmodule:: trio_parallel

.. towncrier release notes start

trio-parallel 1.3.0 (2024-12-25)
--------------------------------

Features
~~~~~~~~

- Advertise support for Python-3.13, although no code changes were made to support it. (`#434 <https://github.com/richardsheridan/trio-parallel/issues/434>`__)
- Add ``kill_on_cancel`` kwarg to :func:`run_sync`. The alias ``cancellable`` will remain indefinitely. (`#437 <https://github.com/richardsheridan/trio-parallel/issues/437>`__)
- Add `cache_scope()`, an async context manager that can override the behavior of
  `trio_parallel.run_sync()` in a subtree of your Trio tasks with an implicit
  `WorkerContext`. (`#455 <https://github.com/richardsheridan/trio-parallel/issues/455>`__)


Deprecations and Removals
~~~~~~~~~~~~~~~~~~~~~~~~~

- Stop advertising support for Python-3.8, although no code changes were made to break it. (`#434 <https://github.com/richardsheridan/trio-parallel/issues/434>`__)
- Removed deprecated ``atexit_shutdown_grace_period``. Use `configure_default_context` to configure the default context shutdown grace period. (`#435 <https://github.com/richardsheridan/trio-parallel/issues/435>`__)


trio-parallel 1.2.4 (2024-12-21)
--------------------------------

Bugfixes
~~~~~~~~

- Ensure worker processes are eagerly reaped after a rare race condition edge case. (`#436 <https://github.com/richardsheridan/trio-parallel/issues/436>`__)
- Fix a usage of a removed internal trio function in the test suite. (`#444 <https://github.com/richardsheridan/trio-parallel/issues/444>`__)


trio-parallel 1.2.3 (2024-10-19)
--------------------------------

Bugfixes
~~~~~~~~

- Fix a regression induced by trio-0.27.0 that causes worker contexts to crash on exit if they happen to wait for jobs to finish. (`#432 <https://github.com/richardsheridan/trio-parallel/issues/432>`__)


trio-parallel 1.2.2 (2024-04-24)
--------------------------------

Bugfixes
~~~~~~~~

- Fixed a rare race condition during cleanup that could trigger unraisable error tracebacks. (`#398 <https://github.com/richardsheridan/trio-parallel/issues/398>`__)
- Made several internal changes that may make compatibility with future Trio versions more stable (`#412 <https://github.com/richardsheridan/trio-parallel/issues/412>`__)


trio-parallel 1.2.1 (2023-11-04)
--------------------------------

Bugfixes
~~~~~~~~

- Resolved a deprecation warning on python 3.12. (`#380 <https://github.com/richardsheridan/trio-parallel/issues/380>`__)


Deprecations and Removals
~~~~~~~~~~~~~~~~~~~~~~~~~

- Although python 3.7 has not been specifically broken, it is no longer tested in CI. (`#389 <https://github.com/richardsheridan/trio-parallel/issues/389>`__)


trio-parallel 1.2.0 (2022-10-29)
--------------------------------

Features
~~~~~~~~

- The behavior of the default context is now fully configurable, superseding ``atexit_shutdown_grace_period`` (`#328 <https://github.com/richardsheridan/trio-parallel/issues/328>`__)


Bugfixes
~~~~~~~~

- Use tblib lazily to pass tracebacks on user exceptions. Previously, tracebacks would only be passed on the built-in python exceptions. (`#332 <https://github.com/richardsheridan/trio-parallel/issues/332>`__)


trio-parallel 1.1.0 (2022-09-18)
--------------------------------

Features
~~~~~~~~

- Add type hints for `run_sync` (`#322 <https://github.com/richardsheridan/trio-parallel/issues/322>`__)
- Use ``tblib`` to enable pickling of tracebacks between processes. Mainly, this
  preserves context of exceptions including chained exceptions. (`#323 <https://github.com/richardsheridan/trio-parallel/issues/323>`__)


Bugfixes
~~~~~~~~

- Prevent Ctrl+C from inducing various leaks and inconsistent states. (`#239 <https://github.com/richardsheridan/trio-parallel/issues/239>`__)
- Cleaned up names/qualnames of objects in the trio_parallel namespace. (`#291 <https://github.com/richardsheridan/trio-parallel/issues/291>`__)


Deprecations and Removals
~~~~~~~~~~~~~~~~~~~~~~~~~

- Removed python 3.6 support (`#236 <https://github.com/richardsheridan/trio-parallel/issues/236>`__)


trio-parallel 1.0.0 (2021-12-04)
--------------------------------

Bugfixes
~~~~~~~~

- Fixed a hang on failed worker subprocess spawns that mostly occurred upon
  accidental multiprocessing recursive spawn. (`#167 <https://github.com/richardsheridan/trio-parallel/issues/167>`__)
- Fixed a hang on Windows when trying to use :meth:`WorkerContext.run_sync` in sequential
  and concurrent Trio runs. (`#171 <https://github.com/richardsheridan/trio-parallel/issues/171>`__)


Improved Documentation
~~~~~~~~~~~~~~~~~~~~~~

- Revamped documentation with tested examples. (`#168 <https://github.com/richardsheridan/trio-parallel/issues/168>`__)


trio-parallel 1.0.0b0 (2021-11-12)
----------------------------------

With this release I consider the project "feature complete".

Features
~~~~~~~~

- Added an API to view statistics about a `WorkerContext`, specifically counting
  ``idle_workers`` and ``running_workers``. (`#155 <https://github.com/richardsheridan/trio-parallel/issues/155>`__)


trio-parallel 1.0.0a2 (2021-10-08)
----------------------------------

Features
~~~~~~~~

- Opportunistically use ``cloudpickle`` to serialize jobs and results. (`#115 <https://github.com/richardsheridan/trio-parallel/issues/115>`__)
- Timeout arguments of :func:`open_worker_context`, ``idle_timeout`` and ``grace_period``,
  now work like trio timeouts, accepting any non-negative `~float` value. (`#116 <https://github.com/richardsheridan/trio-parallel/issues/116>`__)
- Worker process startup is now faster, by importing trio lazily (`#117 <https://github.com/richardsheridan/trio-parallel/issues/117>`__)
- :func:`open_worker_context` now returns a context object that can be used to run
  functions explicitly in a certain context (:meth:`WorkerContext.run_sync`) rather
  than implicitly altering the behavior of :func:`trio_parallel.run_sync`. (`#127 <https://github.com/richardsheridan/trio-parallel/issues/127>`__)


trio-parallel 1.0.0a1 (2021-09-05)
----------------------------------

Features
~~~~~~~~

- Added configuration options for the grace periods permitted to worker caches upon
  shutdown. This includes a new keyword argument for :func:`open_worker_context` and
  a new top level function ``atexit_shutdown_grace_period``. (`#108 <https://github.com/richardsheridan/trio-parallel/issues/108>`__)
- :func:`open_worker_context` gained a new argument, ``init``, and ``retire`` is no longer
  called before the first job in the worker. (`#110 <https://github.com/richardsheridan/trio-parallel/issues/110>`__)


trio-parallel 1.0.0a0 (2021-07-22)
----------------------------------

Features
~~~~~~~~

- The behavior and lifetime of worker processes can now be customized with the :func:`open_worker_context` context manager. (`#19 <https://github.com/richardsheridan/trio-parallel/issues/19>`__)


trio-parallel 0.5.1 (2021-05-05)
--------------------------------

Bugfixes
~~~~~~~~

- Remove ``__version__`` attribute to avoid crash on import when metadata is not available (`#55 <https://github.com/richardsheridan/trio-parallel/issues/55>`__)


trio-parallel 0.5.0 (2021-05-02)
---------------------------------------------------------

Features
~~~~~~~~

- :exc:`trio_parallel.BrokenWorkerError` now contains a reference to the underlying worker process which can be inspected e.g. to handle specific exit codes. (`#48 <https://github.com/richardsheridan/trio-parallel/issues/48>`__)


Bugfixes
~~~~~~~~

- Workers are now fully synchronized with only pipe/channel-like objects, making it impossible to leak semaphores. (`#33 <https://github.com/richardsheridan/trio-parallel/issues/33>`__)
- Fix a regression of a rare race condition where idle workers shut down cleanly but appear broken. (`#43 <https://github.com/richardsheridan/trio-parallel/issues/43>`__)
- Ensure a clean worker shutdown if IPC pipes are closed (`#51 <https://github.com/richardsheridan/trio-parallel/issues/51>`__)


Misc
~~~~

- `#40 <https://github.com/richardsheridan/trio-parallel/issues/40>`__, `#42 <https://github.com/richardsheridan/trio-parallel/issues/42>`__, `#44 <https://github.com/richardsheridan/trio-parallel/issues/44>`__


trio-parallel 0.4.0 (2021-03-25)
--------------------------------

Bugfixes
~~~~~~~~

- Correctly handle the case where `os.cpu_count` returns `None`. (`#32 <https://github.com/richardsheridan/trio-parallel/issues/32>`__)
- Ignore keyboard interrupt (SIGINT) in workers to ensure correct cancellation semantics and clean shutdown on CTRL+C. (`#35 <https://github.com/richardsheridan/trio-parallel/issues/35>`__)


Misc
~~~~

- `#27 <https://github.com/richardsheridan/trio-parallel/issues/27>`__


trio-parallel 0.3.0 (2021-02-21)
--------------------------------

Bugfixes
~~~~~~~~

- Fixed an underlying race condition in IPC. Not a critical bugfix, as it should not be triggered in practice. (`#15 <https://github.com/richardsheridan/trio-parallel/issues/15>`__)
- Reduce the production of zombie children on Unix systems (`#20 <https://github.com/richardsheridan/trio-parallel/issues/20>`__)
- Close internal race condition when waiting for subprocess exit codes on macOS. (`#23 <https://github.com/richardsheridan/trio-parallel/issues/23>`__)
- Avoid a race condition leading to deadlocks when a worker process is killed right after receiving work. (`#25 <https://github.com/richardsheridan/trio-parallel/issues/25>`__)


Improved Documentation
~~~~~~~~~~~~~~~~~~~~~~

- Reorganized documentation for less redundancy and more clarity (`#16 <https://github.com/richardsheridan/trio-parallel/issues/16>`__)


trio-parallel 0.2.0 (2021-02-02)
--------------------------------

Bugfixes
~~~~~~~~

- Changed subprocess context to explicitly always spawn new processes (`#5 <https://github.com/richardsheridan/trio-parallel/issues/5>`__)
- Changed synchronization scheme to achieve full passing tests on

  - Windows, Linux, MacOS
  - CPython 3.6, 3.7, 3.8, 3.9
  - Pypy 3.6, 3.7, 3.7-nightly

  Note Pypy on Windows is not supported here or by Trio (`#10 <https://github.com/richardsheridan/trio-parallel/issues/10>`__)
