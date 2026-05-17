# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
"""commands._ui_renderers — BL-110 PR2 package split.

Original single-namespace module decomposed into a constants
leaf + topologically-layered shared modules + per-surface
modules.  The full original import surface is re-exported here
so every `from ...commands._ui_renderers import X` (and the
`from .._ui_renderers import X` form used within commands/) is
unchanged."""

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from ._layer4 import *
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
    _SUBMODULES = ('_constants', '_layer0', '_layer1', '_layer2', '_layer3', '_layer4', 'today', 'items', 'digest', 'atlas', 'reader')

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        for _s in self._SUBMODULES:
            _m = _sys.modules.get(f'{__name__}.{_s}')
            if _m is not None and hasattr(_m, key):
                setattr(_m, key, value)


_sys.modules[__name__].__class__ = _ViewModelsModule

# Deferred re-exports: these were placed AFTER the defs in the
# original module to break the _ui_renderers <-> reader_home
# import cycle.  Emitting them last (every submodule already
# loaded, so _layout etc. are bound on this package) preserves
# that cycle-break.


# BL-051: ``_render_reader_home`` lives in ``commands/reader_home.py``
# now (file-size cap on this module) — re-exported for back-compat.
from ovp_pipeline.commands.reader_home import _render_reader_home  # noqa: E402,F401
