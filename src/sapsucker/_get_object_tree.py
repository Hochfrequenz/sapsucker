"""Parser for ``GuiSession.GetObjectTree`` JSON output.

Used by :class:`sapsucker.components.base.GuiVContainer.dump_tree` to
bulk-read all element properties via a single COM round-trip instead
of N×21 individual property accesses. The motivation is performance:
on a representative ~277-element SAP screen, the per-property approach
costs roughly 2.9 seconds (~21 properties × ~0.5 ms each, 277 times),
while a single :py:meth:`GuiSession.get_object_tree` call returns the
same data in ~86 ms — a ~34× speedup measured empirically against a
real SAP system.

The JSON shape and the per-element cost are both verified empirically;
see ``unittests/fixtures/get_object_tree_*.json`` for the captured
real-SAP output and ``unittests/test_get_object_tree.py`` for the
parser tests built on top of those fixtures.

This module has no COM dependency. It only knows how to parse a JSON
string into :class:`~sapsucker.models.ElementInfo` objects, so it can
be unit-tested in pure Python without a SAP install.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from sapsucker.models import ElementInfo

__all__ = [
    "DUMP_TREE_PROPS",
    "GetObjectTreeProperties",
    "GetObjectTreeNode",
    "GetObjectTreeResponse",
    "parse_get_object_tree_json",
]


# The 21 properties sapsucker reads on every element. This list is the
# argument to ``GuiSession.GetObjectTree``. Every entry is a simple type
# (String/Integer/Bool) per SAP's ``GuiMagicDispIDs`` enumeration, and
# every entry has been empirically verified to come back populated against
# a real SAP system (BP person create screen, 277 elements, all 21 keys
# present in every node — see ``unittests/fixtures/`` and the original
# probe report in sapsucker issue #20).
DUMP_TREE_PROPS: Final[list[str]] = [
    "Id",
    "Type",
    "TypeAsNumber",
    "Name",
    "Text",
    "Changeable",
    "Tooltip",
    "DefaultTooltip",
    "IconName",
    "Modified",
    "AccText",
    "AccTooltip",
    "AccTextOnRequest",
    "Height",
    "Width",
    "Left",
    "Top",
    "ScreenLeft",
    "ScreenTop",
    "IsSymbolFont",
    "ContainerType",
]


def _coerce_sap_bool(value: Any) -> Any:
    """Coerce a SAP-style bool string to ``bool``, including the empty-string case.

    SAP's :py:meth:`GuiSession.GetObjectTree` returns ALL property values
    as strings — empty string indicates "this property does not apply to
    this element type" (e.g. ``Modified=""`` on a static label, or
    ``IsSymbolFont=""`` on a non-text element). We map that to ``False``
    because the existing per-property COM path returns ``False`` in
    those cases too (its ``_safe_com_attr(child, "Modified", False)``
    falls back to the default when the COM read fails or returns
    ``None``). The two paths must produce identical
    :class:`~sapsucker.models.ElementInfo` shapes.

    Pydantic v2's lax bool parser already handles ``"true"`` / ``"false"``
    / ``"1"`` / ``"0"`` / actual booleans, so we only need to special-case
    the empty string. Anything else passes through unchanged for pydantic
    to handle (or reject loudly if it's truly invalid).

    Verified empirically against real SAP output: bool fields contain
    ``["", "true", "false"]`` in 3 of 4 cases on a typical busy screen.
    See ``unittests/fixtures/get_object_tree_bp_create_full.json``.
    """
    if isinstance(value, str) and value == "":
        return False
    return value


# Annotated bool that pre-processes the SAP empty-string-means-false convention.
# Reused for every bool field on GetObjectTreeProperties.
SapBool = Annotated[bool, BeforeValidator(_coerce_sap_bool)]


class GetObjectTreeProperties(BaseModel):
    """The ``properties`` dict of a single node in GetObjectTree's output.

    SAP returns ALL property values as **strings**, even integers and
    booleans (e.g. ``{"TypeAsNumber": "21", "Changeable": "true",
    "Height": "768"}``). Pydantic v2 in JSON-validation mode coerces
    string-encoded ints automatically (``"21"`` → ``int 21``).

    Bool fields use the :data:`SapBool` annotated type which pre-handles
    the SAP-specific quirk of returning ``""`` (empty string) for "this
    property doesn't apply to this element type" — pydantic's normal lax
    bool parser rejects empty string, so we map it to ``False`` before
    validation.

    Field aliases handle the SAP ``PascalCase`` → Python ``snake_case``
    mapping. Defaults match the defaults in
    :class:`~sapsucker.models.ElementInfo` so missing keys degrade
    gracefully if SAP ever omits a field on some element type.

    ``extra="ignore"`` keeps the model forward-compatible: a new SAP
    property in a future GUI version is silently dropped rather than
    raising a validation error.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(default="", alias="Id")
    type: str = Field(default="", alias="Type")
    type_as_number: int = Field(default=0, alias="TypeAsNumber")
    name: str = Field(default="", alias="Name")
    text: str = Field(default="", alias="Text")
    changeable: SapBool = Field(default=False, alias="Changeable")
    tooltip: str = Field(default="", alias="Tooltip")
    default_tooltip: str = Field(default="", alias="DefaultTooltip")
    icon_name: str = Field(default="", alias="IconName")
    modified: SapBool = Field(default=False, alias="Modified")
    acc_text: str = Field(default="", alias="AccText")
    acc_tooltip: str = Field(default="", alias="AccTooltip")
    acc_text_on_request: str = Field(default="", alias="AccTextOnRequest")
    height: int = Field(default=0, alias="Height")
    width: int = Field(default=0, alias="Width")
    left: int = Field(default=0, alias="Left")
    top: int = Field(default=0, alias="Top")
    screen_left: int = Field(default=0, alias="ScreenLeft")
    screen_top: int = Field(default=0, alias="ScreenTop")
    is_symbol_font: SapBool = Field(default=False, alias="IsSymbolFont")
    container_type: SapBool = Field(default=False, alias="ContainerType")


class GetObjectTreeNode(BaseModel):
    """A single node in GetObjectTree's recursive JSON response.

    Each node carries its own ``properties`` dict and a ``children``
    list of further nodes. Verified empirically against
    ``unittests/fixtures/get_object_tree_bp_create_full.json``.
    """

    properties: GetObjectTreeProperties = Field(default_factory=GetObjectTreeProperties)
    children: list["GetObjectTreeNode"] = Field(default_factory=list)


class GetObjectTreeResponse(BaseModel):
    """Top-level wrapper of GetObjectTree's JSON response.

    Important: the queried element (e.g. ``wnd[0]``) is wrapped one
    level deep — it's at ``response.children[0]``, NOT at the root.
    Its actual descendants are at ``response.children[0].children``.
    This wrapping is unconditional, even when querying a single leaf.
    """

    children: list[GetObjectTreeNode] = Field(default_factory=list)


def parse_get_object_tree_json(raw: str, max_depth: int) -> list[ElementInfo]:
    """Parse a GetObjectTree JSON string into a list of :class:`ElementInfo`.

    The returned list contains the **children of** the queried element,
    not the queried element itself — matching the existing
    :func:`_dump_tree_recursive` semantics so the two paths are
    interchangeable from :meth:`GuiVContainer.dump_tree`'s perspective.

    Uses pydantic's :meth:`BaseModel.model_validate_json` for parsing,
    which delegates to the Rust-backed pydantic-core JSON parser. This
    is faster than ``json.loads`` followed by manual dict access AND
    handles the SAP string-typed-everything quirk (``"21"`` → ``int``,
    ``"true"`` → ``bool``) via pydantic's JSON-mode lax type coercion.

    Args:
        raw: The JSON string returned by
            :meth:`GuiSession.get_object_tree`.
        max_depth: Maximum recursion depth. The JSON tree is descended
            up to this many levels; deeper children are dropped to
            match the per-property path's truncation behaviour.

    Returns:
        A list of :class:`ElementInfo`. Empty when:

        - the queried element had no children, OR
        - the JSON's top-level ``children`` array was empty
          (defensive — should not happen against real SAP), OR
        - ``max_depth`` was 0.
    """
    response = GetObjectTreeResponse.model_validate_json(raw)
    if not response.children:
        return []
    # The queried element is response.children[0]; its descendants are
    # what the existing dump_tree contract returns to callers.
    queried_element = response.children[0]
    return _to_element_info_list(queried_element.children, max_depth, depth=0)


def _to_element_info_list(nodes: list[GetObjectTreeNode], max_depth: int, depth: int) -> list[ElementInfo]:
    """Convert a list of :class:`GetObjectTreeNode` to :class:`ElementInfo` recursively."""
    if depth >= max_depth:
        return []
    return [_to_element_info(node, max_depth, depth) for node in nodes]


def _to_element_info(node: GetObjectTreeNode, max_depth: int, depth: int) -> ElementInfo:
    """Convert a single :class:`GetObjectTreeNode` (with its subtree) to :class:`ElementInfo`."""
    p = node.properties
    return ElementInfo(
        id=p.id,
        type=p.type,
        type_as_number=p.type_as_number,
        name=p.name,
        text=p.text,
        changeable=p.changeable,
        tooltip=p.tooltip,
        default_tooltip=p.default_tooltip,
        icon_name=p.icon_name,
        modified=p.modified,
        acc_text=p.acc_text,
        acc_tooltip=p.acc_tooltip,
        acc_text_on_request=p.acc_text_on_request,
        height=p.height,
        width=p.width,
        left=p.left,
        top=p.top,
        screen_left=p.screen_left,
        screen_top=p.screen_top,
        is_symbol_font=p.is_symbol_font,
        container_type=p.container_type,
        children=_to_element_info_list(node.children, max_depth, depth + 1),
    )
