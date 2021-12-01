# https://stackoverflow.com/a/56813896  CC BY-SA 4.0
# https://stackoverflow.com/a/36295481  CC BY-SA 4.0

import pathlib
import sys

import pytest

scripts = pathlib.Path(__file__).with_name("examples").resolve().glob("*.py")


@pytest.mark.parametrize("script", scripts, ids=lambda x: x.name)
def test_all(script, pytester, capfd):
    result = pytester.run(sys.executable, script, timeout=60)
    assert not result.ret
    # TODO: elegantly assert something about result.stdout
    capfd.readouterr()  # silence successful tests
