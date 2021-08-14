import pytest

pytest_plugins = "pytester"
from pytest_trio.enable_trio_mode import *


import multiprocessing


@pytest.fixture(scope="package")
def manager():
    with multiprocessing.get_context("spawn").Manager() as mgr:
        yield mgr
