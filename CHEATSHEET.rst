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

* Update the version in ``trio_parallel/_version.py``

* Run ``towncrier`` to collect your release notes.

* Review your release notes.

* Check everything in.

* Double-check it all works, docs build, etc.

* Build your sdist and wheel: ``python setup.py sdist bdist_wheel``

* Upload to PyPI: ``twine upload dist/*``

* Use ``git tag`` to tag your version.

* Don't forget to ``git push --tags``.
