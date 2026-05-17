# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
"""ui.view_models — BL-110 package split.

Original single-namespace module decomposed into a constants
leaf + topologically-layered shared modules + per-surface
modules.  The full original import surface is re-exported here
so every `from ...ui.view_models import X` is unchanged."""

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from .today import *
from .items import *
from .digest import *
from .atlas import *
from .reader import *

# Restore single-namespace monkeypatch semantics: the original
# module was one namespace, so callers/tests do
# `monkeypatch.setattr(view_models, X, ...)` and expect every
# caller to see it.  Fan a package-attr write out to whichever
# submodule binds the name (teardown propagates too — it is
# itself a setattr).  Zero call-site / test churn.
import sys as _sys
import types as _types


class _ViewModelsModule(_types.ModuleType):
    _SUBMODULES = ('_constants', '_layer0', '_layer1', '_layer2', '_layer3', 'today', 'items', 'digest', 'atlas', 'reader')

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        for _s in self._SUBMODULES:
            _m = _sys.modules.get(f'{__name__}.{_s}')
            if _m is not None and hasattr(_m, key):
                setattr(_m, key, value)


_sys.modules[__name__].__class__ = _ViewModelsModule
