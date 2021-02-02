Release history
===============

.. currentmodule:: trio_parallel

.. towncrier release notes start

trio_parallel 0.2.0 (2021-02-02)
--------------------------------

Bugfixes
~~~~~~~~

- Changed subprocess context to explicitly always spawn new processes (`#5 <https://github.com/richardsheridan/trio_parallel/issues/5>`__)
- Changed synchronization scheme to achieve full passing tests on

  - Windows, Linux, MacOS
  - CPython 3.6, 3.7, 3.8, 3.9
  - Pypy 3.6, 3.7, 3.7-nightly

  Note Pypy on Windows is not supported here or by Trio (`#10 <https://github.com/richardsheridan/trio_parallel/issues/10>`__)
