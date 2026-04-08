"""Base classes for the SAP GUI component hierarchy."""

# pylint: disable=import-outside-toplevel,broad-exception-caught

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from sapsucker._errors import ElementNotFoundError

if TYPE_CHECKING:
    from sapsucker.components.collection import GuiComponentCollection
    from sapsucker.models import ElementInfo

logger = logging.getLogger(__name__)

__all__ = ["GuiComponent", "GuiContainer", "GuiVComponent", "GuiVContainer"]


class GuiComponent:
    """Wraps the COM GuiComponent interface — the root of the SAP GUI type tree."""

    def __init__(self, com_object: Any) -> None:
        self._com = com_object

    @property
    def com(self) -> Any:
        """Return the underlying COM dispatch object."""
        return self._com

    @property
    def id(self) -> str:
        """Unique technical identifier of this element."""
        return str(self._com.Id)

    @property
    def name(self) -> str:
        """Short name of this element."""
        return str(self._com.Name)

    @property
    def type(self) -> str:
        """SAP GUI type name string."""
        return str(self._com.Type)

    @property
    def type_as_number(self) -> int:
        """Numeric type identifier."""
        return int(self._com.TypeAsNumber)

    @property
    def container_type(self) -> bool:
        """Whether this element can contain children."""
        return bool(self._com.ContainerType)

    @property
    def parent(self) -> Any:
        """Parent COM object in the element hierarchy."""
        return self._com.Parent

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(type={self._com.Type!r}, id={self._com.Id!r})"


class GuiVComponent(GuiComponent):
    """Wraps the COM GuiVComponent interface — visual component with layout properties."""

    @property
    def text(self) -> str:
        """Display text of this element."""
        return str(self._com.Text)

    @text.setter
    def text(self, value: str) -> None:
        self._com.Text = value

    @property
    def tooltip(self) -> str:
        """Tooltip text."""
        return str(self._com.Tooltip)

    @property
    def default_tooltip(self) -> str:
        """Default tooltip text."""
        return str(self._com.DefaultTooltip)

    @property
    def changeable(self) -> bool:
        """Whether the element is currently editable."""
        return bool(self._com.Changeable)

    @property
    def modified(self) -> bool:
        """Whether the element value has been modified."""
        return bool(self._com.Modified)

    @property
    def height(self) -> int:
        """Height in pixels."""
        return int(self._com.Height)

    @property
    def width(self) -> int:
        """Width in pixels."""
        return int(self._com.Width)

    @property
    def left(self) -> int:
        """Left position in pixels."""
        return int(self._com.Left)

    @property
    def top(self) -> int:
        """Top position in pixels."""
        return int(self._com.Top)

    @property
    def screen_left(self) -> int:
        """Absolute screen left position in pixels."""
        return int(self._com.ScreenLeft)

    @property
    def screen_top(self) -> int:
        """Absolute screen top position in pixels."""
        return int(self._com.ScreenTop)

    @property
    def icon_name(self) -> str:
        """Name of the associated icon."""
        return str(self._com.IconName)

    @property
    def is_symbol_font(self) -> bool:
        """Whether the element uses symbol font."""
        return bool(self._com.IsSymbolFont)

    @property
    def acc_text(self) -> str:
        """Accessibility text."""
        return str(self._com.AccText)

    @property
    def acc_tooltip(self) -> str:
        """Accessibility tooltip."""
        return str(self._com.AccTooltip)

    @property
    def acc_text_on_request(self) -> str:
        """Accessibility text available on request."""
        return str(self._com.AccTextOnRequest)

    def set_focus(self) -> None:
        """Set keyboard focus to this element."""
        self._com.SetFocus()

    def visualize(self, on: bool) -> None:
        """Highlight or unhighlight this element."""
        self._com.Visualize(on)

    def dump_state(self, inner_object: str) -> Any:
        """Return a collection of element state properties."""
        return self._com.DumpState(inner_object)


class GuiContainer(GuiComponent):
    """Wraps the COM GuiContainer interface — non-visual container with children."""

    @property
    def children(self) -> GuiComponentCollection:
        """Return the children wrapped in a GuiComponentCollection."""
        from sapsucker.components.collection import GuiComponentCollection

        return GuiComponentCollection(self._com.Children)

    def find_by_id(self, id: str, raise_error: bool = True) -> GuiComponent | None:  # pylint: disable=redefined-builtin
        """Find a child element by its ID path, wrapped in the correct Python class.

        Args:
            id: The SAP GUI element ID path (e.g. 'usr/txtFIELD').
            raise_error: If True (default), raise ElementNotFoundError when not found.

        Returns:
            The wrapped component, or None if not found and raise_error is False.
        """
        from sapsucker._factory import wrap_com_object

        result = self._com.FindById(id, False)
        if result is None:
            logger.debug("element_not_found", extra={"id": id, "parent": self.id})
            if raise_error:
                raise ElementNotFoundError(f"Element not found: {id}")
            return None
        return wrap_com_object(result)


def _safe_com_attr(com_obj: Any, attr: str, default: Any = None) -> Any:
    """Safely get a COM attribute, returning default on any error.

    Unlike getattr(), this catches COM errors (pywintypes.com_error)
    which are not AttributeError and thus bypass getattr's default.
    """
    try:
        return getattr(com_obj, attr)
    except Exception:
        return default


# SAP GUI type numbers for BDT field probe
_BDT_PROBE_TYPES = [
    31,  # GuiTextField
    32,  # GuiCTextField (context/search field)
    33,  # GuiPasswordField
    34,  # GuiComboBox
    42,  # GuiRadioButton
    43,  # GuiCheckBox
    46,  # GuiLabel
]


def _build_element_info(
    child: Any,
    children: list[ElementInfo] | None = None,
    *,
    container_type: bool | None = None,
) -> ElementInfo:
    """Build an ElementInfo from a COM object, reading all available properties.

    Args:
        child: COM object to read properties from.
        children: Pre-built list of child ElementInfo objects.
        container_type: If provided, skip the COM read for ContainerType (avoids
            a redundant read when the caller already checked it for recursion).
    """
    from sapsucker.models import ElementInfo

    return ElementInfo(
        id=str(_safe_com_attr(child, "Id", "")),
        type=str(_safe_com_attr(child, "Type", "")),
        type_as_number=int(_safe_com_attr(child, "TypeAsNumber", 0)),
        name=str(_safe_com_attr(child, "Name", "")),
        text=str(_safe_com_attr(child, "Text", "")),
        changeable=bool(_safe_com_attr(child, "Changeable", False)),
        tooltip=str(_safe_com_attr(child, "Tooltip", "")),
        default_tooltip=str(_safe_com_attr(child, "DefaultTooltip", "")),
        icon_name=str(_safe_com_attr(child, "IconName", "")),
        modified=bool(_safe_com_attr(child, "Modified", False)),
        acc_text=str(_safe_com_attr(child, "AccText", "")),
        acc_tooltip=str(_safe_com_attr(child, "AccTooltip", "")),
        acc_text_on_request=str(_safe_com_attr(child, "AccTextOnRequest", "")),
        height=int(_safe_com_attr(child, "Height", 0)),
        width=int(_safe_com_attr(child, "Width", 0)),
        left=int(_safe_com_attr(child, "Left", 0)),
        top=int(_safe_com_attr(child, "Top", 0)),
        screen_left=int(_safe_com_attr(child, "ScreenLeft", 0)),
        screen_top=int(_safe_com_attr(child, "ScreenTop", 0)),
        is_symbol_font=bool(_safe_com_attr(child, "IsSymbolFont", False)),
        container_type=(
            container_type if container_type is not None else bool(_safe_com_attr(child, "ContainerType", False))
        ),
        children=children if children is not None else [],
    )


def _probe_bdt_fields(com_obj: Any) -> list[ElementInfo]:
    """Discover fields on BDT containers via FindAllByNameEx wildcard.

    BDT-based screens (e.g. BP) don't expose children via the standard
    Children collection. Fields ARE accessible via FindAllByNameEx("*", type_num).
    """
    seen_ids: set[str] = set()
    result: list[ElementInfo] = []
    for type_num in _BDT_PROBE_TYPES:
        try:
            found = com_obj.FindAllByNameEx("*", type_num)
            for j in range(found.Count):
                child = found.Item(j)
                child_id = str(_safe_com_attr(child, "Id", ""))
                if child_id in seen_ids:
                    continue
                seen_ids.add(child_id)
                result.append(_build_element_info(child))
        except Exception:
            pass
    return result


def _count_tree_elements(tree: list[ElementInfo]) -> int:
    """Count total elements in a returned tree (root + all descendants).

    Used by ``GuiVContainer.dump_tree`` to report the size of the dumped
    tree in its perf log line. Pure-Python iteration over the already-built
    ``ElementInfo`` list — no extra COM calls. Microseconds even for trees
    with hundreds of elements.

    Iterative (explicit stack) instead of recursive so the function is
    immune to Python's recursion limit even if ``dump_tree``'s 200-level
    safety cap is ever raised.
    """
    total = 0
    stack: list[ElementInfo] = list(tree)
    while stack:
        elem = stack.pop()
        total += 1
        if elem.children:
            stack.extend(elem.children)
    return total


def _measure_tree_depth(tree: list[ElementInfo]) -> int:
    """Return the maximum depth of an already-built tree (1-indexed; 0 if empty).

    Used by ``GuiVContainer.dump_tree`` to report the actual depth reached
    in its perf log line — which may be less than the requested ``max_depth``
    if the tree is shallower, or equal if the cap was hit.

    Iterative (explicit stack) instead of recursive so the function is
    immune to Python's recursion limit even if ``dump_tree``'s 200-level
    safety cap is ever raised. Each stack entry carries its own depth so
    the running max can be computed in a single pass.
    """
    max_depth = 0
    stack: list[tuple[ElementInfo, int]] = [(elem, 1) for elem in tree]
    while stack:
        elem, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if elem.children:
            stack.extend((child, depth + 1) for child in elem.children)
    return max_depth


_SAPSUCKER_DISABLE_FAST_PATH_ENV = "SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH"
_FAST_PATH_DISABLE_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def _read_fast_path_disabled_from_env() -> bool:
    """Return True if the env var disables the fast path, False otherwise.

    Truthy values that disable the fast path: ``"1"``, ``"true"``, ``"yes"``,
    ``"on"`` (case-insensitive). Anything else — unset, ``"0"``, ``"false"``,
    empty string, garbage, typos — leaves the fast path enabled.

    The allowlist is intentionally small. The cost of a false negative
    (typo, fast path stays on, user crashes) is one test crash. The cost
    of a false positive (typo, fast path silently disabled) is a 50× perf
    regression that doesn't show up in any error log. Strictly worse —
    so we err on the side of staying fast.
    """
    raw = os.environ.get(_SAPSUCKER_DISABLE_FAST_PATH_ENV, "")
    return raw.strip().lower() in _FAST_PATH_DISABLE_TRUTHY_VALUES


_SESSION_TYPE_NAME = "GuiSession"
_MAX_PARENT_WALK = 10

# Process-global cache: True once we have observed a fast-path failure that
# is permanent (e.g. older SAP GUI without GetObjectTree, or a SAP version
# where GetObjectTree consistently raises). Set by ``GuiVContainer.dump_tree``
# the first time the fast path fails. Subsequent calls skip the fast-path
# attempt entirely and go straight to the slow path, avoiding the per-call
# overhead of `_find_session_com` + a doomed `GetObjectTree` call.
#
# Reviewer I4 on PR #22: without this cache, every dump_tree call on a
# system without `GetObjectTree` pays ~22 wasted COM round-trips before
# falling back. With it, the cost is amortized to one wasted attempt
# per process lifetime.
#
# Caveat: this is a *process-global* flag, not a per-session cache. The
# trade-off is simplicity (no need to key on session COM identity, which
# is unstable across reconnects). The downside is that if a single process
# talks to two SAP servers — one new enough for GetObjectTree and one
# older — the older server's first failure disables the fast path for
# BOTH. This is acceptable because the more common scenario is a single
# SAP system per process. Reset for testing via ``_reset_fast_path_cache``.
#
# Initial value (0.4.1+): the env var
# ``SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH`` (allowlist:
# ``1/true/yes/on`` case-insensitive) lets environments hitting SAP Note
# 3674808 force the fast path off at process startup. The flag still
# behaves as a runtime cache for the AttributeError path described
# above; the env var only sets its initial value. Per-call override is
# also available via ``GuiVContainer.dump_tree(use_fast_path=...)``;
# see that method's docstring.
_fast_path_permanently_disabled: bool = _read_fast_path_disabled_from_env()  # pylint: disable=invalid-name


def _reset_fast_path_cache() -> None:
    """Reset the fast-path-disabled cache. Used by unit tests; not public API.

    Re-reads ``SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH`` from the
    environment so tests that mutate the env var via
    ``monkeypatch.setenv`` see the new value after calling this.
    """
    global _fast_path_permanently_disabled  # noqa: PLW0603  pylint: disable=global-statement
    _fast_path_permanently_disabled = _read_fast_path_disabled_from_env()


def _is_permanent_fast_path_failure(exc: BaseException) -> bool:
    """Heuristic: should this exception class disable the fast path globally?

    True for "the SAP GUI on this server simply doesn't have GetObjectTree" —
    i.e. AttributeError when calling the method, or COM errors that indicate
    "method not found / member not found / interface not supported". These
    errors are deterministic per server, so caching is safe.

    False for transient errors (network blip, timeout, JSON parse error on
    a one-off bad response, the SAP Note 3674808 crash). Those should be
    retried on the next call.

    The heuristic is intentionally conservative: when in doubt we treat the
    error as transient (return False) so we keep retrying. The cost of a
    false negative is one wasted fast-path attempt per call; the cost of a
    false positive is permanently disabling the fast path on a working
    system. Better to retry forever than to disable forever.
    """
    if isinstance(exc, AttributeError):
        # `obj.GetObjectTree` raised AttributeError → method does not exist
        # on the COM dispatch interface for this SAP version. Permanent.
        return True
    # pywintypes.com_error with the "member not found" pattern would also
    # qualify, but identifying it portably (across pywin32 versions and
    # COM error code conventions) is brittle — we leave that as a transient
    # error and rely on the second-and-subsequent calls also failing fast
    # via the existing try/except. Net cost: a few wasted COM round-trips
    # per call on legacy SAP, vs. zero on the AttributeError path which is
    # what most legacy SAP installs actually emit.
    return False


def _find_session_com(any_com: Any) -> Any | None:
    """Walk up to the GuiSession COM proxy from any descendant element.

    Used by :meth:`GuiVContainer.dump_tree` to obtain the
    :class:`GuiSession` needed for the bulk-read fast path
    (:func:`_dump_tree_via_get_object_tree`). Returns ``None`` on any
    failure — in which case the caller falls back to the per-property
    slow path. Never raises.

    Implementation note: walks ``.Parent`` until it finds an element
    whose ``Type`` is ``"GuiSession"``. We deliberately do NOT use
    ``FindById("/app/con[N]/ses[M]")`` because ``FindById`` on a
    descendant element resolves IDs **relative to that element**, not
    absolutely from the application root — verified empirically: SAP
    raises "The control could not be found by id." when an absolute
    path is passed to ``FindById`` on a child element.

    The Parent walk is bounded by ``_MAX_PARENT_WALK`` (10) which is
    far above the realistic SAP tree depth from any leaf to its
    session (typically 4-7 hops). Each hop is one COM round-trip, so
    the total cost is small compared to the property-bulk-read we
    are about to do.
    """
    current = any_com
    for _ in range(_MAX_PARENT_WALK):
        try:
            type_name = str(current.Type)
        except Exception:
            return None
        if type_name == _SESSION_TYPE_NAME:
            return current
        try:
            parent = current.Parent
        except Exception:
            return None
        if parent is None:
            return None
        current = parent
    return None


def _dump_tree_via_get_object_tree(self_com: Any, container_id: str, max_depth: int) -> list[ElementInfo]:
    """Bulk-read the entire subtree via ``GuiSession.GetObjectTree`` (fast path).

    Single COM round-trip regardless of tree size. Returns the children
    of the queried container (not the container itself), matching the
    :func:`_dump_tree_recursive` contract so the two paths are
    interchangeable from :meth:`GuiVContainer.dump_tree`'s perspective.

    Raises if no GuiSession can be located, if the GetObjectTree call
    itself fails, or if the returned JSON cannot be parsed. The caller
    catches all of these and falls back to the per-property slow path,
    so a fast-path failure becomes slow-but-functional, never broken.
    """
    from sapsucker._get_object_tree import (  # pylint: disable=import-outside-toplevel
        DUMP_TREE_PROPS,
        parse_get_object_tree_json,
    )

    session_com = _find_session_com(self_com)
    if session_com is None:
        raise RuntimeError("could not locate GuiSession from this element")
    raw_json = str(session_com.GetObjectTree(container_id, DUMP_TREE_PROPS))
    return parse_get_object_tree_json(raw_json, max_depth)


def _dump_tree_recursive(com_obj: Any, depth: int, max_depth: int) -> list[ElementInfo]:
    """Recursively walk COM children and build a list of ElementInfo."""
    result: list[ElementInfo] = []
    try:
        children_com = com_obj.Children
        count = children_com.Count
    except Exception:
        count = 0  # BDT containers throw here — treat as empty

    if count > 0:
        for i in range(count):
            try:
                child = children_com.Item(i)
            except Exception:
                continue
            is_container = bool(_safe_com_attr(child, "ContainerType", False))
            child_children = (
                _dump_tree_recursive(child, depth + 1, max_depth) if depth + 1 < max_depth and is_container else []
            )
            result.append(_build_element_info(child, child_children, container_type=is_container))
    elif _safe_com_attr(com_obj, "ContainerType", False):
        # BDT fallback: probe for hidden fields when container has no standard children
        obj_id = str(_safe_com_attr(com_obj, "Id", ""))
        if "/usr" in obj_id.lower():
            result.extend(_probe_bdt_fields(com_obj))

    return result


class GuiVContainer(GuiContainer, GuiVComponent):
    """Wraps the COM GuiVContainer interface — visual container with children and layout."""

    def dump_tree(
        self,
        max_depth: int | None = None,
        *,
        use_fast_path: bool | None = None,
    ) -> list[ElementInfo]:
        """Return a recursive tree of ElementInfo for all children.

        Args:
            max_depth: Maximum recursion depth. None means unlimited (with a
                       hard safety cap of 200 to prevent infinite recursion).
            use_fast_path: Per-call override for the GetObjectTree fast path
                       (keyword-only). ``None`` (default) respects the
                       process-level decision: enabled by default, disabled
                       if ``SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH`` is
                       set in the environment, or disabled if a previous
                       call observed an AttributeError. ``True`` forces the
                       fast path attempt for this call only. ``False`` forces
                       the slow path for this call only. The kwarg does NOT
                       mutate the process-level decision; subsequent calls
                       with the default ``None`` see the unchanged module
                       flag. See sapsucker#23 for context.

        Tries the **fast path** first: a single
        :py:meth:`GuiSession.GetObjectTree` call (SAP GUI for Windows
        ≥ 7.70 PL3) returns all 21 element properties for the entire
        subtree as a JSON string in one COM round-trip. On a typical
        ~280-element SAP screen this is ~30× faster than the per-property
        path that this method historically used.

        On any exception in the fast path — older SAP GUI without the
        method, the SAP Note 3674808 crash bug, an unexpected JSON
        shape, a stale COM proxy that can't resolve the GuiSession,
        etc. — the method silently falls back to the per-property
        :func:`_dump_tree_recursive` path. The fallback log line at
        DEBUG level (``dump_tree_fast_path_failed_falling_back``)
        lets us monitor frequency post-deploy. A fast-path failure
        therefore becomes slow-but-functional, never broken.

        Emits one INFO log line per call (``event=dump_tree``) with the
        wall-clock duration, the number of elements in the returned tree,
        the actual depth reached, the requested ``max_depth`` (or ``None``
        if unbounded), the container's COM ID, and which path was taken
        (``"fast"`` or ``"slow"``). Used by downstream consumers (e.g.
        sapwebgui.mcp) to correlate slow tool calls with the cost of
        building the tree. The instrumentation overhead is microseconds —
        pure-Python iteration over the already-built ``ElementInfo`` list,
        no extra COM calls.
        """
        global _fast_path_permanently_disabled  # noqa: PLW0603  pylint: disable=global-statement

        effective_depth_cap = max_depth if max_depth is not None else 200
        start = time.perf_counter()

        # Read the container ID once — used for both the GetObjectTree
        # call (which needs an ID to query from) and the perf log line.
        # Wrapped in try/except so a stale proxy never breaks the dump.
        try:
            container_id = str(self._com.Id)
        except Exception:
            container_id = "<unknown>"

        result: list[ElementInfo]
        path: str
        # Decide whether to ATTEMPT the fast path on this call.
        # Precedence: per-call ``use_fast_path`` kwarg > module flag.
        # See sapsucker#23 — environments hitting the SAP Note 3674808
        # native crash bug can disable the fast path globally via the
        # SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH env var, or
        # per-call via ``use_fast_path=False``.
        if use_fast_path is False:
            fast_path_attempt = False
        elif use_fast_path is True:
            fast_path_attempt = True
        else:
            fast_path_attempt = not _fast_path_permanently_disabled

        # Fast path: bulk-read via GuiSession.GetObjectTree.
        # Skip the attempt entirely if (a) the caller or env disabled it,
        # (b) a previous call has already proven the fast path is permanently
        # unsupported on this process's SAP version (e.g. SAP GUI < 7.70 PL3),
        # or (c) we couldn't read the container ID. See
        # ``_fast_path_permanently_disabled``.
        if not fast_path_attempt or container_id == "<unknown>":
            result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
            path = "slow"
        else:
            try:
                result = _dump_tree_via_get_object_tree(self._com, container_id, effective_depth_cap)
                path = "fast"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if _is_permanent_fast_path_failure(exc):
                    # Cache the verdict so subsequent calls skip the doomed
                    # attempt. Log at WARNING (not DEBUG) once on the
                    # transition because it's a meaningful environment
                    # discovery, not just a routine fallback.
                    _fast_path_permanently_disabled = True
                    logger.warning(
                        "dump_tree_fast_path_permanently_disabled",
                        extra={"reason": type(exc).__name__, "container_id": container_id},
                    )
                else:
                    logger.debug("dump_tree_fast_path_failed_falling_back", exc_info=True)
                result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
                path = "slow"

        duration_ms = int((time.perf_counter() - start) * 1000)

        logger.info(
            "dump_tree",
            extra={
                "duration_ms": duration_ms,
                "elements": _count_tree_elements(result),
                "depth_reached": _measure_tree_depth(result),
                "max_depth_param": max_depth,
                "container_id": container_id,
                "path": path,
            },
        )
        return result

    def find_by_name(self, name: str, type_name: str) -> GuiComponent | None:
        """Find the first child element matching name and type string. Returns None if not found."""
        from sapsucker._factory import wrap_com_object

        result = self._com.FindByName(name, type_name)
        return wrap_com_object(result) if result is not None else None

    def find_by_name_ex(self, name: str, type_number: int) -> GuiComponent | None:
        """Find the first child element matching name and type number. Returns None if not found."""
        from sapsucker._factory import wrap_com_object

        result = self._com.FindByNameEx(name, type_number)
        return wrap_com_object(result) if result is not None else None

    def find_all_by_name(self, name: str, type_name: str) -> GuiComponentCollection:
        """Find all child elements matching name and type string."""
        from sapsucker.components.collection import GuiComponentCollection

        return GuiComponentCollection(self._com.FindAllByName(name, type_name))

    def find_all_by_name_ex(self, name: str, type_number: int) -> GuiComponentCollection:
        """Find all child elements matching name and type number."""
        from sapsucker.components.collection import GuiComponentCollection

        return GuiComponentCollection(self._com.FindAllByNameEx(name, type_number))
