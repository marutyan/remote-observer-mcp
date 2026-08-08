"""Read-only semantic observers and extension registration."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

_CORE_MODULES = {"system"}


def register_extension_tools(server: Any, config: Any) -> None:
    """Register tool providers shipped inside this trusted package."""
    names = sorted(
        module.name
        for module in pkgutil.iter_modules(__path__)
        if module.name not in _CORE_MODULES and not module.name.startswith("_")
    )
    for name in names:
        module = importlib.import_module(f"{__name__}.{name}")
        registrar = getattr(module, "register_tools", None)
        if callable(registrar):
            registrar(server, config)
