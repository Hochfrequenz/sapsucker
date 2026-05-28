"""COM object dispatch — importable by all component modules without circularity.

wrap_com_object lives here, not in _factory.py, so that component modules can
import it at module level without creating a circular dependency.

_factory.py populates the dispatch tables by calling _set_dispatch_tables() at
the end of its own module body.  sapsucker/components/__init__.py imports
_factory at its bottom, which guarantees registration happens before any
component method can call wrap_com_object.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sapsucker.components.base import GuiComponent

logger = logging.getLogger(__name__)

_type_map: dict[int, type] = {}
_shell_subtype_map: dict[str, type] = {}
_shell_type_num: int = 122
_fallback_cls: type | None = None


def _set_dispatch_tables(
    type_map: dict[int, type],
    shell_subtype_map: dict[str, type],
    shell_type_num: int,
    fallback_cls: type,
) -> None:
    """Called once by _factory.py after all component classes are defined."""
    global _type_map, _shell_subtype_map, _shell_type_num, _fallback_cls
    _type_map = type_map
    _shell_subtype_map = shell_subtype_map
    _shell_type_num = shell_type_num
    _fallback_cls = fallback_cls


def wrap_com_object(com_obj: Any) -> GuiComponent:
    """Wrap a raw COM dispatch object in the appropriate Python class.

    Uses two-level dispatch:
    1. TypeAsNumber selects the base class.
    2. For GuiShell (type 122), SubType refines to the concrete shell class.
    3. Unknown types fall back to GuiComponent.
    """
    type_num = com_obj.TypeAsNumber
    cls = _type_map.get(type_num)
    if cls is not None and type_num == _shell_type_num:
        sub_type = getattr(com_obj, "SubType", "")
        cls = _shell_subtype_map.get(sub_type, cls)
    elif cls is None:
        cls = _fallback_cls
        logger.debug(
            "unknown_type_fallback",
            extra={"type_as_number": type_num, "com_type": getattr(com_obj, "Type", "?")},
        )
    return cls(com_obj)  # type: ignore[misc]
