# https://stackoverflow.com/a/56813896  CC BY-SA 4.0
# https://stackoverflow.com/a/36295481  CC BY-SA 4.0

import pathlib
import subprocess
import sys

import pytest

scripts = pathlib.Path(__file__).with_name("examples").resolve().glob("*.py")


@pytest.mark.parametrize("script", scripts, ids=lambda x: x.name)
def test_all(script, capfd):
    subprocess.run([sys.executable, str(script)], check=True, timeout=60)
    # TODO: elegantly assert something about stdout
