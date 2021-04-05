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

* Install requirements: ``pip install -e .[test]``
  (possibly in a virtualenv)

* Actually run the tests: ``pytest --pyargs trio_parallel``


To run black
------------

* Show what changes black wants to make: ``black --diff setup.py
  trio_parallel``

* Apply all changes directly to the source tree: ``black setup.py
  trio_parallel``


To make a release
-----------------

* Run ``towncrier`` to collect your release notes.

* Review your release notes.

* Double-check it all works, docs build, etc.

* Check everything in.

* Make a release PR on GitHub. Checks must pass.

* Use GitHub release mechanism to tag the release commit: ``hub release create {version}``

* Build your sdist and wheel: ``python -m build``

* Check wheel and sdist, especially version in filenames.

* Upload to PyPI: ``twine upload dist/*``

* Upload to GitHub: ``hub release edit -a dist/*.whl``
