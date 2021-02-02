import cffi
import re
import enum

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

BOOL GetNamedPipeHandleStateA(
  HANDLE  hNamedPipe,
  LPDWORD lpState,
  LPDWORD lpCurInstances,
  LPDWORD lpMaxCollectionCount,
  LPDWORD lpCollectDataTimeout,
  LPSTR   lpUserName,
  DWORD   nMaxUserNameSize
);

"""

# cribbed from pywincffi
# programmatically strips out those annotations MSDN likes, like _In_
REGEX_SAL_ANNOTATION = re.compile(
    r"\b(_In_|_Inout_|_Out_|_Outptr_|_Reserved_)(opt_)?\b"
)
LIB = REGEX_SAL_ANNOTATION.sub(" ", LIB)

# Other fixups:
# - get rid of FAR, cffi doesn't like it
LIB = re.sub(r"\bFAR\b", " ", LIB)
# - PASCAL is apparently an alias for __stdcall (on modern compilers - modern
#   being _MSC_VER >= 800)
LIB = re.sub(r"\bPASCAL\b", "__stdcall", LIB)

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


class PipeModes(enum.IntFlag):
    PIPE_WAIT = 0x00000000
    PIPE_NOWAIT = 0x00000001
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_READMODE_MESSAGE = 0x00000002


################################################################
# Generic helpers
################################################################


def _handle(obj):  # pragma: no cover
    # For now, represent handles as either cffi HANDLEs or as ints.  If you
    # try to pass in a file descriptor instead, it's not going to work
    # out. (For that msvcrt.get_osfhandle does the trick, but I don't know if
    # we'll actually need that for anything...) For sockets this doesn't
    # matter, Python never allocates an fd. So let's wait until we actually
    # encounter the problem before worrying about it.
    if type(obj) is int:
        return ffi.cast("HANDLE", obj)
    else:
        return obj


def raise_winerror(winerror=None, *, filename=None, filename2=None):  # pragma: no cover
    if winerror is None:
        winerror, msg = ffi.getwinerror()
    else:
        _, msg = ffi.getwinerror(winerror)
    # https://docs.python.org/3/library/exceptions.html#OSError
    raise OSError(0, msg, filename, winerror, filename2)


def peek_pipe_message_left(handle):
    left = ffi.new("LPDWORD")
    if not kernel32.PeekNamedPipe(
        _handle(handle), ffi.NULL, 0, ffi.NULL, ffi.NULL, left
    ):
        raise_winerror()  # pragma: no cover
    return left[0]
