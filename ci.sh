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

if python -c 'import sys; sys.exit(sys.version_info >= (3, 7))'; then
    # Python < 3.7, select last ipython with 3.6 support
    # macOS requires the suffix for --in-place or you get an undefined label error
    sed -i'.bak' 's/ipython==[^ ]*/ipython==7.16.1/' test-requirements.txt
    sed -i'.bak' 's/traitlets==[^ ]*/traitlets==4.3.3/' test-requirements.txt
    git diff test-requirements.txt
fi

if [ "$CHECK_FORMATTING" = "1" ]; then
    python -m pip install -r test-requirements.txt
    source check.sh
else
    # Actual tests
    python -m pip install -r test-requirements.txt

    # So we can run the test for our apport/excepthook interaction working
    if [ -e /etc/lsb-release ] && grep -q Ubuntu /etc/lsb-release; then
        sudo apt install -q python3-apport
    fi

    # We run the tests from inside an empty directory, to make sure Python
    # doesn't pick up any .py files from our working dir. Might have been
    # pre-created by some of the code above.
    mkdir empty || true
    cd empty

    INSTALLDIR=$(python -c "import os, trio_parallel; print(os.path.dirname(trio_parallel.__file__))")
    cp ../setup.cfg $INSTALLDIR
    # We have to copy .coveragerc into this directory, rather than passing
    # --cov-config=../.coveragerc to pytest, because codecov.sh will run
    # 'coverage xml' to generate the report that it uses, and that will only
    # apply the ignore patterns in the current directory's .coveragerc.
    cp ../.coveragerc .
    if pytest -W error -r a --junitxml=../test-results.xml ${INSTALLDIR} --cov="$INSTALLDIR" --verbose; then
        PASSED=true
    else
        PASSED=false
    fi

    # The codecov docs recommend something like 'bash <(curl ...)' to pipe the
    # script directly into bash as its being downloaded. But, the codecov
    # server is flaky, so we instead save to a temp file with retries, and
    # wait until we've successfully fetched the whole script before trying to
    # run it.
    curl-harder -o codecov.sh https://codecov.io/bash
    bash codecov.sh -n "${JOB_NAME}"

    $PASSED
fi
