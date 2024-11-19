# Vendored from outcome in v1.3.0 under identical MIT/Apache2 license.
# Copyright Contributors to the outcome project.


class Outcome:
    def __init__(self):
        self._unwrapped = False

    def _set_unwrapped(self) -> None:
        assert not self._unwrapped, "An Outcome can only be unwrapped once."
        object.__setattr__(self, "_unwrapped", True)


class Value(Outcome):
    """Concrete :class:`Outcome` subclass representing a regular value."""

    def __init__(self, value):
        self.value = value
        super().__init__()

    def unwrap(self):
        self._set_unwrapped()
        return self.value


class Error(Outcome):
    """Concrete :class:`Outcome` subclass representing a raised exception."""

    def __init__(self, error):
        self.error = error
        super().__init__()

    def unwrap(self):
        self._set_unwrapped()
        # Tracebacks show the 'raise' line below out of context, so let's give
        # this variable a name that makes sense out of context.
        captured_error = self.error
        try:
            raise captured_error
        finally:
            # We want to avoid creating a reference cycle here. Python does
            # collect cycles just fine, so it wouldn't be the end of the world
            # if we did create a cycle, but the cyclic garbage collector adds
            # latency to Python programs, and the more cycles you create, the
            # more often it runs, so it's nicer to avoid creating them in the
            # first place. For more details see:
            #
            #    https://github.com/python-trio/trio/issues/1770
            #
            # In particular, by deleting this local variables from the 'unwrap'
            # methods frame, we avoid the 'captured_error' object's
            # __traceback__ from indirectly referencing 'captured_error'.
            del captured_error, self


def capture(sync_fn, *args, **kwargs):
    """Run ``sync_fn(*args, **kwargs)`` and capture the result.

    Returns:
      Either a :class:`Value` or :class:`Error` as appropriate.

    """
    try:
        return Value(sync_fn(*args, **kwargs))
    except BaseException as exc:  # noqa: ASYNC103
        return Error(exc.with_traceback(exc.__traceback__.tb_next))  # noqa: ASYNC104
