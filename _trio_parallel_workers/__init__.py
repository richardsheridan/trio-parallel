"""Isolated package for trio-parallel's worker behavior

This allows workers to start up without "heavy" dependencies like trio and attrs.
Users still need to make sure their CPU-bound functions also do not pull in such
packages, but at least we are doing our part."""

import signal
import sys
from inspect import iscoroutine
from pickle import HIGHEST_PROTOCOL
from time import perf_counter


if getattr(sys, "pypy_version_info", (8,)) < (7, 3, 10):
    # mute rogue pypy debug print statement
    try:
        import _winapi
    except ImportError:
        pass
    else:
        _winapi.print = lambda *args, **kwargs: None

try:
    from cloudpickle import dumps, loads
except ImportError:
    from pickle import dumps, loads

from outcome import capture, Error
from tblib.pickling_support import install as install_pickling_support

MAX_TIMEOUT = 24.0 * 60.0 * 60.0
ACK = b"\x06"


def handle_job(job):
    try:
        fn, args = loads(job)
        ret = fn(*args)
        if iscoroutine(ret):
            # Manually close coroutine to avoid RuntimeWarnings
            ret.close()
            raise TypeError(
                "trio-parallel worker expected a sync function, but {!r} appears "
                "to be asynchronous".format(getattr(fn, "__qualname__", fn))
            )
        return ret
    except BaseException as e:
        install_pickling_support(e)
        raise e


def safe_dumps(result):
    try:
        return dumps(result, protocol=HIGHEST_PROTOCOL)
    except BaseException as exc:  # noqa: TRIO103
        return dumps(Error(exc), protocol=HIGHEST_PROTOCOL)  # noqa: TRIO104


def safe_poll(recv_pipe, timeout):
    deadline = perf_counter() + timeout
    while timeout > MAX_TIMEOUT:
        if recv_pipe.poll(MAX_TIMEOUT):
            return True
        timeout = deadline - perf_counter()
    else:
        return recv_pipe.poll(timeout)


def worker_behavior(recv_pipe, send_pipe, idle_timeout, init, retire):
    # Intercept keyboard interrupts to avoid passing KeyboardInterrupt
    # between processes. (Trio will take charge via cancellation.)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        if isinstance(init, bytes):  # true except on "fork"
            # Signal successful startup to spawn/forkserver parents.
            send_pipe.send_bytes(ACK)
            init = loads(init)
        if isinstance(retire, bytes):  # true except on "fork"
            retire = loads(retire)
        init()
        while safe_poll(recv_pipe, idle_timeout):
            send_pipe.send_bytes(
                safe_dumps(capture(handle_job, recv_pipe.recv_bytes()))
            )
            if retire():
                break
    except (BrokenPipeError, EOFError):
        # Graceful shutdown: If the main process closes the pipes, we will
        # observe one of these exceptions and can simply exit quietly.
        # Closing pipes manually fixed some __del__ flakiness in CI
        send_pipe.close()
        recv_pipe.close()
        return
    except BaseException:
        # Ensure BrokenWorkerError raised in the main proc.
        send_pipe.close()
        # recv_pipe must remain open and clear until the main proc closes it.
        try:
            while True:
                recv_pipe.recv_bytes()
        except EOFError:
            pass
        raise
    else:
        # Clean idle shutdown or retirement: close recv_pipe first to minimize
        # subsequent race.
        recv_pipe.close()
        # Race condition: it is possible to sneak a write through in the main process
        # between the while loop predicate and recv_pipe.close(). Naively, this would
        # make a clean shutdown look like a broken worker. By sending a sentinel
        # value, we can indicate to a waiting main process that we have hit this
        # race condition and need a restart. However, the send MUST be non-blocking
        # to free this process's resources in a timely manner. Therefore, this message
        # can be any size on Windows but must be less than 512 bytes by POSIX.1-2001.
        send_pipe.send_bytes(dumps(None, protocol=HIGHEST_PROTOCOL))
        send_pipe.close()
