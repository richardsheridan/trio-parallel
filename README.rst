=======================================
trio-parallel: CPU parallelism for Trio
=======================================

Do you have CPU-bound work that just keeps slowing down your Trio_ event loop no
matter what you try? Do you need to get all those cores humming at once? This is the
library for you!

The aim of trio-parallel is to use the lightest-weight, lowest-overhead, lowest-latency
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
            nursery.start_soon(trio_parallel.run_sync, hard_work, 2, True)
            nursery.start_soon(trio_parallel.run_sync, hard_work, 1, False)
            nursery.start_soon(too_slow)
            result = await trio_parallel.run_sync(hard_work, 1.5, None)
            nursery.cancel_scope.cancel()
        print("got", result, "in", time.perf_counter() - t0, "seconds")
        # prints 2.xxx


    if __name__ == "__main__":
        multiprocessing.freeze_support()
        trio.run(amain)


Additional examples_ and the full API_ are available in the documentation_

Features
--------

- Bypasses the GIL for CPU-bound work
- Minimal API complexity

  - looks and feels like Trio threads_

- Minimal internal complexity

  - No reliance on ``multiprocessing.Pool``, ``ProcessPoolExecutor``, or any background threads

- Cross-platform
- ``print`` just works
- Automatic, opportunistic use of cloudpickle_
- Automatic LIFO caching of subprocesses
- Cancel seriously misbehaving code

  - currently via SIGKILL/TerminateProcess

- Convert segfaults and other scary things to catchable errors

FAQ
---

How does trio-parallel run Python code in parallel?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Currently, this project is based on ``multiprocessing`` subprocesses and
has all the usual multiprocessing caveats_ (``freeze_support``, pickleable objects
only). The case for basing these workers on multiprocessing is that it keeps a lot of
complexity outside of the project while offering a set of quirks that users are
likely already familiar with.
The pickling limitations can be partially alleviated by installing cloudpickle_.

Can I have my workers talk to each other?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is currently possible through the use of ``multiprocessing.Manager``,
but we don't and will not officially support it.

This package focuses on providing
a flat hierarchy of worker subprocesses to run synchronous, CPU-bound functions.
If you are looking to create a nested hierarchy of processes communicating
asynchronously with each other, while preserving the power, safety, and convenience of
structured concurrency, look into `tractor <https://github.com/goodboy/tractor>`_.
Or, if you are looking for a more customized solution, try using ``trio.run_process``
to spawn additional Trio runs and have them talk to each other over sockets.

Can I let my workers outlive the main Trio process?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The worker processes are started with the ``daemon`` flag for lifetime management,
so this use case is not supported.

How should I map a function over a collection of arguments?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is fully possible but we leave the implementation of that up to you. Think
of us as a `loky <https://loky.readthedocs.io/en/stable/index.html>`_ for your
`joblib <https://joblib.readthedocs.io/en/latest/>`_, but natively async and Trionic.
Some example parallelism patterns can be found in the documentation_.
Also, look into `trimeter <https://github.com/python-trio/trimeter>`_?

Contributing
------------
If you notice any bugs, need any help, or want to contribute any code, GitHub issues_
and pull requests are very welcome! Please read the `code of conduct`_.

.. _chat: https://gitter.im/python-trio/general
.. |chat badge| image:: https://img.shields.io/badge/chat-join%20now-blue.svg?color=royalblue&logo=Gitter
   :target: `chat`_
   :alt: Chatroom

.. _forum: https://trio.discourse.group
.. |forum badge| image:: https://img.shields.io/badge/forum-join%20now-blue.svg?color=royalblue&logo=Discourse
   :target: `forum`_
   :alt: Forum

.. _documentation: https://trio-parallel.readthedocs.io/
.. |documentation badge| image:: https://img.shields.io/readthedocs/trio-parallel?logo=readthedocs&logoColor=whitesmoke
   :target: `documentation`_
   :alt: Documentation

.. _distribution: https://pypi.org/project/trio-parallel/
.. |version badge| image:: https://img.shields.io/pypi/v/trio-parallel?logo=PyPI&logoColor=whitesmoke
   :target: `distribution`_
   :alt: Latest Pypi version

.. _pypistats: https://pypistats.org/packages/trio-parallel
.. |pypistats badge| image:: https://img.shields.io/pypi/dm/trio-parallel?logo=pypi&logoColor=whitesmoke
   :target: `pypistats`_
   :alt: Pypi monthly downloads

.. _pepy: https://pepy.tech/project/trio-parallel
.. |pepy badge| image:: https://pepy.tech/badge/trio-parallel/month
   :target: `pepy`_
   :alt: Pypi monthly downloads

.. |python versions badge| image:: https://img.shields.io/pypi/pyversions/trio-parallel.svg?logo=PyPI&logoColor=whitesmoke
   :alt: Supported Python versions
   :target: `distribution`_

.. |python interpreters badge| image:: https://img.shields.io/pypi/implementation/trio-parallel.svg?logo=PyPI&logoColor=whitesmoke
   :alt: Supported Python interpreters
   :target: `distribution`_

.. _issues: https://github.com/richardsheridan/trio-parallel/issues
.. |issues badge| image:: https://img.shields.io/github/issues-raw/richardsheridan/trio-parallel?logo=github
   :target: `issues`_
   :alt: Issues

.. _repository: https://github.com/richardsheridan/trio-parallel
.. |repository badge| image:: https://img.shields.io/github/last-commit/richardsheridan/trio-parallel?logo=github
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
.. |style badge| image:: https://img.shields.io/badge/code%20style-Black-black
   :target: `style`_
   :alt: Code style

.. _license: https://github.com/richardsheridan/trio-parallel/blob/main/LICENSE
.. |license badge| image:: https://img.shields.io/pypi/l/trio-parallel?color=informational
   :target: `license`_
   :alt: MIT -or- Apache License 2.0

.. _cloudpickle: https://github.com/cloudpipe/cloudpickle
.. _API: https://trio-parallel.readthedocs.io/en/latest/reference.html
.. _examples: https://trio-parallel.readthedocs.io/en/latest/examples.html
.. _threads: https://trio.readthedocs.io/en/stable/reference-core.html#trio.to_thread.run_sync
.. _caveats: https://docs.python.org/3/library/multiprocessing.html#programming-guidelines
.. _Trio: https://github.com/python-trio/trio
.. _code of conduct: https://trio.readthedocs.io/en/stable/code-of-conduct.html