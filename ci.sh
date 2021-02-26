#!/bin/bash

set -ex -o pipefail

# Log some general info about the environment
uname -a
env | sort

python -c "import sys, struct, ssl; print('#' * 70); print('python:', sys.version); print('version_info:', sys.version_info); print('bits:', struct.calcsize('P') * 8); print('#' * 70)"
rm -rf trio_parallel
python -m pip install -U pip
python -m pip install ./*.whl

python -m pip install -r test-requirements.txt

INSTALLDIR=$(python -c "import os, trio_parallel; print(os.path.dirname(trio_parallel.__file__))")

pytest --pyargs trio_parallel._tests -W error -r a --cov-report xml "${INSTALLDIR}" --cov="$INSTALLDIR" --verbose
