#!/bin/bash

set -ex -o pipefail

# Log some general info about the environment
uname -a
env | sort

# Curl's built-in retry system is not very robust; it gives up on lots of
# network errors that we want to retry on. Wget might work better, but it's
# not installed on azure pipelines's windows boxes. So... let's try some good
# old-fashioned brute force. (This is also a convenient place to put options
# we always want, like -f to tell curl to give an error if the server sends an
# error response, and -L to follow redirects.)
function curl-harder() {
    for BACKOFF in 0 1 2 4 8 15 15 15 15; do
        sleep $BACKOFF
        if curl -fL --connect-timeout 5 "$@"; then
            return 0
        fi
    done
    return 1
}

################################################################
# We have a Python environment!
################################################################

python -c "import sys, struct, ssl; print('#' * 70); print('python:', sys.version); print('version_info:', sys.version_info); print('bits:', struct.calcsize('P') * 8); print('openssl:', ssl.OPENSSL_VERSION, ssl.OPENSSL_VERSION_INFO); print('#' * 70)"

python -m pip install -U pip setuptools wheel
python -m pip --version

python setup.py sdist --formats=zip
python -m pip install dist/*.zip

python -m pip install -r test-requirements.txt

# We run the tests from inside an empty directory, to make sure Python
# doesn't pick up any .py files from our working dir. Might have been
# pre-created by some of the code above.
mkdir empty || true
cd empty

INSTALLDIR=$(python -c "import os, trio_parallel; print(os.path.dirname(trio_parallel.__file__))")
cp ../setup.cfg $INSTALLDIR

pytest -W error -r a --cov-config=../pyproject.toml --cov-report xml "${INSTALLDIR}" --cov="$INSTALLDIR" --verbose
