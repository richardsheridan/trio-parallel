Tips
====

If you want to use static typing (mypy) in your project
-------------------------------------------------------

  * Update ``install_requires`` in ``setup.py`` to include ``"trio-typing"``
    (assuming you use it).

  * Uncomment the dependency on ``mypy`` in ``test-requirements.txt``.

  * Uncomment the mypy invocation in ``check.sh``.

  * Create an empty ``trio_parallel/py.typed`` file,
    and add ``"include trio_parallel/py.typed"`` to
    ``MANIFEST.in``.

To run tests
------------

* Install test extras: ``pip install -e .[test]``
  (possibly in a virtualenv)

* Actually run the tests: ``pytest --pyargs trio_parallel``


To run black
------------

* Show what changes black wants to make: ``black --diff trio_parallel``

* Apply all changes directly to the source tree: ``black trio_parallel``


To update pinned requirements
-----------------------------

* Run ``pip install pip-compile-multi`` if necessary.

* Run ``pip-compile-multi --allow-unsafe``.

* Note that manually changing dependencies will fail a CI check.


To make a release
-----------------

* Run ``towncrier build --version {version}`` to collect your release notes.

* Review your release notes.

* Double-check it all works, docs build, etc.

* Check everything in.

* Make a release PR on GitHub. Checks must pass.

* Use GitHub release mechanism to tag the release PR merge commit:
  ``gh release create {version}``

  * This triggers an action to release on PyPI and GitHub as well.
