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
_SHELL_TYPE_NUM: int = 122
_FALLBACK_CLS: type | None = None


def _set_dispatch_tables(
    type_map: dict[int, type],
    shell_subtype_map: dict[str, type],
    shell_type_num: int,
    fallback_cls: type,
) -> None:
    """Called once by _factory.py after all component classes are defined."""
    global _type_map, _shell_subtype_map, _SHELL_TYPE_NUM, _FALLBACK_CLS  # pylint: disable=global-statement
    _type_map = type_map
    _shell_subtype_map = shell_subtype_map
    _SHELL_TYPE_NUM = shell_type_num
    _FALLBACK_CLS = fallback_cls


def wrap_com_object(com_obj: Any) -> GuiComponent:
    """Wrap a raw COM dispatch object in the appropriate Python class.

    Uses two-level dispatch:
    1. TypeAsNumber selects the base class.
    2. For GuiShell (type 122), SubType refines to the concrete shell class.
    3. Unknown types fall back to GuiComponent.
    """
    type_num = com_obj.TypeAsNumber
    cls = _type_map.get(type_num)
    if cls is not None and type_num == _SHELL_TYPE_NUM:
        sub_type = getattr(com_obj, "SubType", "")
        cls = _shell_subtype_map.get(sub_type, cls)
    elif cls is None:
        cls = _FALLBACK_CLS
        logger.debug(
            "unknown_type_fallback",
            extra={"type_as_number": type_num, "com_type": getattr(com_obj, "Type", "?")},
        )
    return cls(com_obj)  # type: ignore[misc, no-any-return]


def com_collection_item(com_collection: Any, index: int) -> Any:
    """Return the raw COM element at *index* from a SAP GUI COM collection.

    Works around SAP GUI COM error 618, ``"Bad index type for collection
    access."``, which some hosts raise for ``collection.Item(<int>)`` under
    pywin32 late binding **even when the collection is perfectly healthy**.

    Observed on a fresh Windows 11 / SAP GUI 8.0 host (Hochfrequenz/sapgui.mcp#804):
    ``connection.Children.Count == 1``, yet ``Children.Item(0)`` raises 618 while
    ``Children(0)`` (the collection's default member) and ``Children.ElementAt(0)``
    both return the element fine. The same code works on other hosts, so the
    difference is in how the integer index is marshalled to ``Item`` — not the
    collection, the element, or the server. This silently broke ``login()``:
    ``wait_for_session`` reads ``conn.children[0]``, the ``Item`` call raised, and a
    broad ``except`` swallowed it, so login spun to a 30s timeout with no session.

    Strategy, in order (first that succeeds wins):
      1. ``Item(index)`` — the historical path. Tried first so behaviour is
         byte-for-byte identical on the (majority of) hosts where it already
         works; this fix can only *add* success cases, never remove one.
      2. ``ElementAt(index)`` — SAP's documented integer accessor; accepted on
         hosts that reject ``Item``'s marshalled index.
      3. default member ``collection(index)`` — last resort; empirically accepted
         wherever ``ElementAt`` is.

    If every strategy fails, the ORIGINAL ``Item`` error is re-raised, so a
    genuinely bad index still surfaces its real COM error rather than a masked one.
    """
    try:
        return com_collection.Item(index)
    except Exception:  # pylint: disable=broad-exception-caught
        # ``getattr(..., None)`` returns a live method proxy for late-bound
        # CDispatch objects (so this branch is reached on real SAP hosts), and
        # ``None`` for plain objects that genuinely lack the method.
        element_at = getattr(com_collection, "ElementAt", None)
        if element_at is not None:
            try:
                return element_at(index)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        try:
            return com_collection(index)  # default member: collection(index)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        # Bare ``raise`` (not ``raise <saved exc>``) so the ORIGINAL traceback from
        # the failed ``Item(index)`` call is preserved. Once the inner fallback
        # handlers have completed, the exception being handled here is again the
        # original ``Item`` error, so this re-raises it — not a fallback's error.
        raise
