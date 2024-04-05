import enum

import cffi

################################################################
# Functions and types
################################################################

LIB = """
BOOL PeekNamedPipe(
  HANDLE  hNamedPipe,
  LPVOID  lpBuffer,
  DWORD   nBufferSize,
  LPDWORD lpBytesRead,
  LPDWORD lpTotalBytesAvail,
  LPDWORD lpBytesLeftThisMessage
);
"""

ffi = cffi.FFI()
ffi.cdef(LIB)

kernel32 = ffi.dlopen("kernel32.dll")

################################################################
# Magic numbers
################################################################

# Here's a great resource for looking these up:
#   https://www.magnumdb.com
# (Tip: check the box to see "Hex value")

INVALID_HANDLE_VALUE = ffi.cast("HANDLE", -1)


class ErrorCodes(enum.IntEnum):
    STATUS_TIMEOUT = 0x102
    WAIT_TIMEOUT = 0x102
    WAIT_ABANDONED = 0x80
    WAIT_OBJECT_0 = 0x00  # object is signaled
    WAIT_FAILED = 0xFFFFFFFF
    ERROR_IO_PENDING = 997
    ERROR_OPERATION_ABORTED = 995
    ERROR_ABANDONED_WAIT_0 = 735
    ERROR_INVALID_HANDLE = 6
    ERROR_INVALID_PARMETER = 87
    ERROR_NOT_FOUND = 1168
    ERROR_NOT_SOCKET = 10038
    ERROR_MORE_DATA = 234


################################################################
# Generic helpers
################################################################


# vendored from trio, so no coverage
def raise_winerror(winerror=None, *, filename=None, filename2=None):  # pragma: no cover
    if winerror is None:
        winerror, msg = ffi.getwinerror()
    else:
        _, msg = ffi.getwinerror(winerror)
    # https://docs.python.org/3/library/exceptions.html#OSError
    raise OSError(0, msg, filename, winerror, filename2)


def peek_pipe_message_left(handle: int):
    # If you try to pass in a file descriptor instead, it's not going to work out.
    assert type(handle) is int
    handle = ffi.cast("HANDLE", handle)
    left = ffi.new("LPDWORD")
    if not kernel32.PeekNamedPipe(handle, ffi.NULL, 0, ffi.NULL, ffi.NULL, left):
        raise_winerror()  # pragma: no cover
    return left[0]
