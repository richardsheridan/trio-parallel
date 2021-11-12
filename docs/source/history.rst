Release history
===============

.. currentmodule:: trio_parallel

.. towncrier release notes start

trio-parallel 1.0.0b0 (2021-11-12)
----------------------------------

With this release I consider the project "feature complete" although, as a beta
release still, I would be open to PRs for new features with a strong motivation.

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
  a new top level function :func:`atexit_shutdown_grace_period`. (`#108 <https://github.com/richardsheridan/trio-parallel/issues/108>`__)
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
