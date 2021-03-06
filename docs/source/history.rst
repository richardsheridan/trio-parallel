Release history
===============

.. currentmodule:: trio_parallel

.. towncrier release notes start

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
