=======================================
trio-parallel: CPU parallelism for Trio
=======================================

Do you have CPU bound work that just keeps slowing down your event loop no matter
what you try? Do you need to get all those cores humming at once?
This is the library for you!

The aim of trio-parallel is to use the lightest-weight, lowest-overhead, lowest latency
method to achieve CPU parallelism of arbitrary Python code with a dead-simple API.

Resources
---------

=============  =============================

License        |license badge|
Documentation  |documentation badge|
Chat           |chat badge|
Forum          |forum badge|
Issues         |issues badge|
Repository     |repository badge|
Tests          |tests badge|
Coverage       |coverage badge|
Style          |style badge|
Distribution   | |version badge|
               | |python versions badge|
               | |python interpreters badge|

=============  =============================

Example
-------

.. code-block:: python

    import multiprocessing
    import trio
    import trio_parallel
    import time


    def hard_work(n, x):
        t = time.perf_counter() + n
        y = x
        while time.perf_counter() < t:
            x = not x
        print(y, "transformed into", x)
        return x


    async def too_slow():
        await trio_parallel.run_sync(hard_work, 20, False, cancellable=True)


    async def amain():
        t0 = time.perf_counter()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(trio_parallel.run_sync, hard_work, 3, True)
            nursery.start_soon(trio_parallel.run_sync, hard_work, 1, False)
            nursery.start_soon(too_slow)
            result = await trio_parallel.run_sync(hard_work, 2, None)
            nursery.cancel_scope.cancel()
        print("got", result, "in", time.perf_counter() - t0, "seconds")


    if __name__ == "__main__":
        multiprocessing.freeze_support()
        trio.run(amain)


The full API is documented at `<https://trio-parallel.readthedocs.io/>`__

Features
--------

- Bypasses the GIL for CPU bound work
- Minimal API complexity (looks and feels like Trio threads)
- Cross-platform
- Automatic LIFO caching of subprocesses
- Cancel seriously misbehaving code

  - currently via SIGKILL/TerminateProcess

- Convert segfaults and other scary things to catchable errors

FAQ
---

How does trio-parallel run Python code in parallel?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Currently, this project is based on ``multiprocessing`` subprocesses and
has all the usual multiprocessing caveats (``freeze_support``, pickleable objects only).
The case for basing these workers on
multiprocessing is that it keeps a lot of complexity outside of the project while
offering a set of quirks that users are likely already familiar with.

Can I have my workers talk to each other?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is currently possible through the use of ``multiprocessing.Manager``,
but we don't and will not support it. Instead, try using ``trio.run_process`` and
having the various Trio runs talk to each other over sockets. Also, look into
`tractor <https://github.com/goodboy/tractor>`_?

Can I let my workers outlive the main Trio process?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The worker processes are started with the ``daemon`` flag for lifetime management,
so this use case is not supported.

How should I map a function over a collection of arguments?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is fully possible but we leave the implementation of that up to you.
Also, look into `trimeter <https://github.com/python-trio/trimeter>`_?

Contributing
------------
If you notice any bugs, need any help, or want to contribute any code,
GitHub issues and pull requests are very welcome! Please read the
`code of conduct <CODE_OF_CONDUCT.md>`_.

Motiviation
-----------


This project's aim is to use the lightest-weight, lowest-overhead, lowest latency
method to achieve parallelism of arbitrary Python code, and make it natively async for Trio.
Given that Python (and CPython in particular) has ongoing difficulties parallelizing
CPU-bound work in threads, this package provides :func:`trio_parallel.run_sync` to dispatch
synchronous function execution to *subprocesses*. However, this project is not at all constrained by that,
and will be considering subinterpreters, or any other avenue as they become available.

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

.. _chat: https://gitter.im/python-trio/general
.. |chat badge| image:: https://img.shields.io/badge/chat-join%20now-blue.svg?color=royalblue&logo=Gitter&logoColor=whitesmoke
   :target: `chat`_
   :alt: Chatroom

.. _forum: https://trio.discourse.group
.. |forum badge| image:: https://img.shields.io/badge/forum-join%20now-blue.svg?color=royalblue&logo=Discourse&logoColor=whitesmoke
   :target: `forum`_
   :alt: Forum

.. _documentation: https://trio-parallel.readthedocs.io/
.. |documentation badge| image:: https://readthedocs.org/projects/trio-parallel/badge/
   :target: `documentation`_
   :alt: Documentation

.. _distribution: https://pypi.org/project/trio-parallel/
.. |version badge| image:: https://badgen.net/pypi/v/trio-parallel?icon=pypi
   :target: `distribution`_
   :alt: Latest Pypi version

.. _pypistats: https://pypistats.org/packages/trio-parallel
.. |pypistats badge| image:: https://img.shields.io/pypi/dm/trio-parallel?logo=pypi&logoColor=whitesmoke
   :target: `pypistats`_
   :alt: Pypi monthly downloads

.. _pepy: https://pepy.tech/badge/trio-parallel
.. |pepy badge| image:: https://pepy.tech/badge/trio-parallel
   :target: `pepy`_
   :alt: Pypi total downloads

.. |python versions badge| image:: https://img.shields.io/pypi/pyversions/trio-parallel.svg?color=indianred&logo=PyPI&logoColor=whitesmoke
   :alt: Supported Python versions
   :target: `distribution`_

.. |python interpreters badge| image:: https://img.shields.io/pypi/implementation/trio-parallel.svg?color=indianred&logo=PyPI&logoColor=whitesmoke
   :alt: Supported Python interpreters
   :target: `distribution`_

.. _issues: https://github.com/richardsheridan/trio-parallel/issues
.. |issues badge| image:: https://badgen.net/github/open-issues/richardsheridan/trio-parallel?icon=github
   :target: `issues`_
   :alt: Issues

.. _repository: https://github.com/richardsheridan/trio-parallel
.. |repository badge| image:: https://badgen.net/github/last-commit/richardsheridan/trio-parallel/main?icon=github
   :target: `repository`_
   :alt: Repository

.. _tests: https://github.com/richardsheridan/trio-parallel/actions?query=branch%3Amain
.. |tests badge| image:: https://img.shields.io/github/workflow/status/richardsheridan/trio-parallel/CI/main?logo=GitHub-Actions&logoColor=whitesmoke
   :target: `tests`_
   :alt: Tests

.. _coverage: https://codecov.io/gh/richardsheridan/trio-parallel
.. |coverage badge| image:: https://codecov.io/gh/richardsheridan/trio-parallel/branch/main/graph/badge.svg?token=EQqs2abxxG
   :target: `coverage`_
   :alt: Test coverage

.. _style: https://github.com/psf/black
.. |style badge| image:: https://badgen.net/badge/code%20style/Black/black
   :target: `style`_
   :alt: Code style

.. _license: https://github.com/richardsheridan/trio-parallel/blob/main/LICENSE
.. |license badge| image:: https://badgen.net/pypi/license/trio-parallel
   :target: `license`_
   :alt: MIT -or- Apache License 2.0