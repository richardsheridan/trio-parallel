#!/bin/bash

set -ex -o pipefail

# Log some general info about the environment
uname -a
env | sort

python -c "import sys, struct, ssl; print('#' * 70); print('python:', sys.version); print('version_info:', sys.version_info); print('bits:', struct.calcsize('P') * 8); print('#' * 70)"

python -m pip install -U pip setuptools wheel
python -m pip --version

python setup.py sdist --formats=zip
python -m pip install dist/*.zip
rm -rf trio_parallel

python -m pip install -r test-requirements.txt

INSTALLDIR=$(python -c "import os, trio_parallel; print(os.path.dirname(trio_parallel.__file__))")

pytest -W error -r a --cov-report xml "${INSTALLDIR}" --cov="$INSTALLDIR" --verbose
