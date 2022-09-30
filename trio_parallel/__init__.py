"""trio-parallel: CPU parallelism for Trio"""

from ._impl import (
    run_sync,
    open_worker_context,
    WorkerContext,
    WorkerType,
    current_default_worker_limiter,
    configure_default_context,
    atexit_shutdown_grace_period,
    default_context_statistics,
)
from ._abc import BrokenWorkerError


# Vendored from trio._util in v0.20.0 under identical MIT/Apache2 license.
# Copyright Contributors to the Trio project.
def fixup_module_metadata(module_name, namespace):
    seen_ids = set()

    def fix_one(qualname, name, obj):
        # avoid infinite recursion (relevant when using
        # typing.Generic, for example)
        if id(obj) in seen_ids:
            return
        seen_ids.add(id(obj))

        mod = getattr(obj, "__module__", None)
        if mod is not None and mod.startswith("trio_parallel."):
            obj.__module__ = module_name
            # Modules, unlike everything else in Python, put fully-qualified
            # names into their __name__ attribute. Trio checks for "." to avoid
            # rewriting these, but we don't have any, so it's always true.
            nodot = hasattr(obj, "__name__") and "." not in obj.__name__
            if nodot:  # pragma: no branch
                obj.__name__ = name
                obj.__qualname__ = qualname
            if isinstance(obj, type):
                for attr_name, attr_value in obj.__dict__.items():
                    fix_one(objname + "." + attr_name, attr_name, attr_value)

    for objname, obj in namespace.items():
        if not objname.startswith("_"):  # ignore private attributes
            fix_one(objname, objname, obj)


fixup_module_metadata(__name__, globals())
del fixup_module_metadata
