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

    import functools
    import multiprocessing
    import trio
    import trio_parallel


    def loop(n):
        # Arbitrary CPU-bound work
        for _ in range(n):
            pass
        print("Loops completed:", n)


    async def amain():
        t0 = trio.current_time()
        async with trio.open_nursery() as nursery:
            # Do CPU-bound work in parallel
            for i in [6, 7, 8] * 4:
                nursery.start_soon(trio_parallel.run_sync, loop, 10 ** i)
            # Event loop remains responsive
            t1 = trio.current_time()
            await trio.sleep(0)
            print("Scheduling latency:", trio.current_time() - t1)
            # This job could take far too long, make it cancellable!
            nursery.start_soon(
                functools.partial(
                    trio_parallel.run_sync, loop, 10 ** 20, cancellable=True
                )
            )
            await trio.sleep(2)
            # Only explicitly cancellable jobs are killed on cancel
            nursery.cancel_scope.cancel()
        print("Total runtime:", trio.current_time() - t0)


    if __name__ == "__main__":
        multiprocessing.freeze_support()
        trio.run(amain)


Additional examples and the full API are available in the documentation_.

Features
--------

- Bypasses the GIL for CPU-bound work
- Minimal API complexity

  - looks and feels like Trio threads_

- Minimal internal complexity

  - No reliance on ``multiprocessing.Pool``, ``ProcessPoolExecutor``, or any background threads

- Cross-platform
- ``print`` just works
- Seamless interoperation with

  - coverage.py_
  - viztracer_
  - cloudpickle_

- Automatic LIFO caching of subprocesses
- Cancel seriously misbehaving code via SIGKILL/TerminateProcess

- Convert segfaults and other scary things to catchable errors

FAQ
---

How does trio-parallel run Python code in parallel?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Currently, this project is based on ``multiprocessing`` subprocesses and
has all the usual multiprocessing caveats_ (``freeze_support``, pickleable objects
only, executing the ``__main__`` module).
The case for basing these workers on multiprocessing is that it keeps a lot of
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

No. Trio's structured concurrency strictly bounds job runs to within a given
``trio.run`` call, while cached idle workers are shutdown and killed if necessary
by our ``atexit`` handler, so this use case is not supported.

How should I map a function over a collection of arguments?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This is fully possible but we leave the implementation of that up to you. Think
of us as a `loky <https://loky.readthedocs.io/en/stable/index.html>`_ for your
`joblib <https://joblib.readthedocs.io/en/latest/>`_, but natively async and Trionic.
We take care of the worker handling so that you can focus on the best concurrency
for your application. That said, some example parallelism patterns can be found in
the documentation_.

Also, look into `aiometer <https://github.com/florimondmanca/aiometer>`_?

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

.. _coverage.py: https://coverage.readthedocs.io/
.. _viztracer: https://viztracer.readthedocs.io/
.. _cloudpickle: https://github.com/cloudpipe/cloudpickle
.. _threads: https://trio.readthedocs.io/en/stable/reference-core.html#trio.to_thread.run_sync
.. _caveats: https://docs.python.org/3/library/multiprocessing.html#programming-guidelines
.. _Trio: https://github.com/python-trio/trio
.. _code of conduct: https://trio.readthedocs.io/en/stable/code-of-conduct.html