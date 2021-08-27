import multiprocessing
import pytest
from pytest_trio.enable_trio_mode import *


@pytest.fixture(scope="package")
def manager():
    with multiprocessing.get_context("spawn").Manager() as mgr:
        yield mgr
