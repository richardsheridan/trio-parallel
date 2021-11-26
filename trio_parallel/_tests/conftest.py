import multiprocessing
import pytest
from pytest_trio.enable_trio_mode import *

pytest_plugins = "pytester"


@pytest.fixture(scope="package")
def manager():
    with multiprocessing.get_context("spawn").Manager() as mgr:
        yield mgr
