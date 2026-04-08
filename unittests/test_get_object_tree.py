"""Tests for the GuiSession.GetObjectTree-based fast path of dump_tree.

Two layers:

1. **Parser layer** (``sapsucker._get_object_tree``): tests that the
   pydantic-based JSON parser correctly converts a real-SAP-captured
   JSON fixture into ``ElementInfo`` objects, including the
   string-to-int and SAP-empty-string-to-False quirks. The fixture
   ``unittests/fixtures/get_object_tree_bp_create_full.json`` is
   actual output from a live SAP BP person-create screen, captured
   by ``scripts/probe_get_object_tree.py``.

2. **dump_tree integration**: tests that ``GuiVContainer.dump_tree``
   uses the fast path when ``GetObjectTree`` succeeds and falls back
   to the per-property path when it raises. Mocks the COM session.
"""

# pylint: disable=protected-access

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sapsucker.components.base as _base_module
from sapsucker._get_object_tree import (
    DUMP_TREE_PROPS,
    GetObjectTreeNode,
    GetObjectTreeProperties,
    GetObjectTreeResponse,
    parse_get_object_tree_json,
)
from sapsucker.components.base import (
    GuiVContainer,
    _find_session_com,
    _is_permanent_fast_path_failure,
    _reset_fast_path_cache,
)
from sapsucker.models import ElementInfo
from unittests.conftest import make_mock_com

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
BP_FULL = FIXTURES / "get_object_tree_bp_create_full.json"
BP_IDS_ONLY = FIXTURES / "get_object_tree_bp_create_ids_only.json"


# ---------------------------------------------------------------------------
# DUMP_TREE_PROPS list — pinned for backward compatibility
# ---------------------------------------------------------------------------


class TestDumpTreePropsList:
    def test_contains_all_21_sapsucker_known_properties(self):
        """The DUMP_TREE_PROPS list must mirror _build_element_info exactly.

        If sapsucker ever adds a 22nd property to ElementInfo, this test
        forces the props list to be updated too.
        """
        expected = {
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
        }
        assert set(DUMP_TREE_PROPS) == expected
        assert len(DUMP_TREE_PROPS) == 21


# ---------------------------------------------------------------------------
# GetObjectTreeProperties — type coercion of SAP string-typed values
# ---------------------------------------------------------------------------


class TestGetObjectTreeProperties:
    def test_coerces_string_int(self):
        """SAP returns ints as strings (e.g. "21") — pydantic must coerce them."""
        props = GetObjectTreeProperties.model_validate({"TypeAsNumber": "21", "Height": "768"})
        assert props.type_as_number == 21
        assert isinstance(props.type_as_number, int)
        assert props.height == 768
        assert isinstance(props.height, int)

    def test_coerces_string_bool_true_false(self):
        """SAP returns bools as "true"/"false" — pydantic's lax coercion handles these."""
        props = GetObjectTreeProperties.model_validate({"Changeable": "true", "ContainerType": "false"})
        assert props.changeable is True
        assert props.container_type is False

    def test_empty_string_bool_coerces_to_false(self):
        """SAP returns "" for bool properties that don't apply to this element type.

        The existing per-property COM path returns False as the default in
        these cases. The two paths must produce identical ElementInfo shapes,
        so we map empty string to False here. This is the
        SAP-specific quirk that motivated the SapBool annotated type.
        """
        props = GetObjectTreeProperties.model_validate({"Modified": "", "IsSymbolFont": "", "Changeable": ""})
        assert props.modified is False
        assert props.is_symbol_font is False
        assert props.changeable is False

    def test_missing_keys_use_defaults(self):
        """An element type may omit a property entirely — fall back to defaults."""
        props = GetObjectTreeProperties.model_validate({"Id": "x"})
        assert props.id == "x"
        assert props.type == ""
        assert props.type_as_number == 0
        assert props.changeable is False
        assert props.height == 0

    def test_extra_keys_ignored(self):
        """A future SAP version adding a new property must NOT break the parser."""
        props = GetObjectTreeProperties.model_validate({"Id": "x", "UnknownNewSapProperty": "whatever"})
        assert props.id == "x"

    def test_missing_keys_use_defaults_all_21_fields(self):
        """Reviewer M5: extend the defaults test to cover ALL 21 fields, not just 5.

        A future refactor that drops a default for any field would be caught
        by this test. Constructs the model from a single-key dict and asserts
        every other field has the documented default.
        """
        props = GetObjectTreeProperties.model_validate({"Id": "only-this-one"})
        assert props.id == "only-this-one"
        # 4 strings (other than id):
        assert props.type == ""
        assert props.name == ""
        assert props.text == ""
        # 7 ints (after the 4 strings already covered above):
        assert props.type_as_number == 0
        assert props.height == 0
        assert props.width == 0
        assert props.left == 0
        assert props.top == 0
        assert props.screen_left == 0
        assert props.screen_top == 0
        # 4 SapBool fields:
        assert props.changeable is False
        assert props.modified is False
        assert props.is_symbol_font is False
        # 1 strict bool field (container_type, locked down per reviewer I3):
        assert props.container_type is False
        # 6 more strings:
        assert props.tooltip == ""
        assert props.default_tooltip == ""
        assert props.icon_name == ""
        assert props.acc_text == ""
        assert props.acc_tooltip == ""
        assert props.acc_text_on_request == ""

    def test_container_type_is_strict_bool_rejects_empty_string(self):
        """Reviewer I3: container_type must reject "" (which would silently
        misclassify a container as non-container, dropping its entire subtree).

        The other three bool fields tolerate "" via SapBool because real SAP
        does emit "" for them on element types where the property doesn't
        apply (verified empirically against the BP fixture). ContainerType
        is never observed empty — strict bool surfaces any future divergence
        loudly via fallback to slow path instead of silently producing
        wrong results.
        """
        with pytest.raises(Exception):  # noqa: PT011 — pydantic ValidationError or pydantic_core.ValidationError
            GetObjectTreeProperties.model_validate({"ContainerType": ""})

    def test_container_type_strict_bool_accepts_true_false(self):
        """ContainerType still works for the documented values."""
        true_case = GetObjectTreeProperties.model_validate({"ContainerType": "true"})
        false_case = GetObjectTreeProperties.model_validate({"ContainerType": "false"})
        assert true_case.container_type is True
        assert false_case.container_type is False


# ---------------------------------------------------------------------------
# parse_get_object_tree_json — against synthetic JSON
# ---------------------------------------------------------------------------


class TestParseGetObjectTreeJsonSynthetic:
    def test_empty_top_level_children_returns_empty_list(self):
        result = parse_get_object_tree_json('{"children": []}', max_depth=200)
        assert result == []

    def test_single_node_no_children(self):
        raw = json.dumps(
            {
                "children": [
                    {
                        "properties": {"Id": "/app/con[0]/ses[0]/wnd[0]", "Type": "GuiMainWindow"},
                        "children": [],
                    }
                ]
            }
        )
        result = parse_get_object_tree_json(raw, max_depth=200)
        # Returns CHILDREN OF the queried element, not the element itself.
        # An element with no children -> empty list.
        assert result == []

    def test_multi_top_level_children_raises(self):
        """Reviewer I2: SAP's documented contract returns exactly one
        top-level wrapper. If GetObjectTree ever returns more than one,
        the parser must NOT silently drop entries — it must raise so
        dump_tree falls back to the per-property slow path.
        """
        raw = json.dumps(
            {
                "children": [
                    {"properties": {"Id": "first"}, "children": []},
                    {"properties": {"Id": "second"}, "children": []},
                ]
            }
        )
        with pytest.raises(ValueError, match="top-level"):
            parse_get_object_tree_json(raw, max_depth=200)

    def test_two_top_level_children_returned(self):
        raw = json.dumps(
            {
                "children": [
                    {
                        "properties": {"Id": "wnd[0]"},
                        "children": [
                            {"properties": {"Id": "wnd[0]/usr", "Type": "GuiUserArea"}, "children": []},
                            {"properties": {"Id": "wnd[0]/sbar", "Type": "GuiStatusbar"}, "children": []},
                        ],
                    }
                ]
            }
        )
        result = parse_get_object_tree_json(raw, max_depth=200)
        assert len(result) == 2
        assert result[0].id == "wnd[0]/usr"
        assert result[0].type == "GuiUserArea"
        assert result[1].id == "wnd[0]/sbar"
        assert result[1].type == "GuiStatusbar"

    def test_max_depth_truncates(self):
        raw = json.dumps(
            {
                "children": [
                    {
                        "properties": {"Id": "wnd[0]"},
                        "children": [
                            {
                                "properties": {"Id": "L1"},
                                "children": [
                                    {
                                        "properties": {"Id": "L2"},
                                        "children": [
                                            {"properties": {"Id": "L3"}, "children": []},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        # Full descent
        full = parse_get_object_tree_json(raw, max_depth=200)
        assert full[0].id == "L1"
        assert full[0].children[0].id == "L2"
        assert full[0].children[0].children[0].id == "L3"

        # Cap at 1: only L1 returned, no children
        capped = parse_get_object_tree_json(raw, max_depth=1)
        assert len(capped) == 1
        assert capped[0].id == "L1"
        assert capped[0].children == []

    def test_five_level_nesting_with_max_depth_variants(self):
        """5-level chain — max_depth=2 truncates at level 2; max_depth=200 descends all 5.

        The acceptance criteria in issue #20 explicitly call out the
        5-level nesting case, separately from the 3-level case above.
        """

        def make_chain(level: int, total: int) -> dict:
            """Recursively build a chain of `total` levels labeled L1, L2, ..."""
            return {
                "properties": {"Id": f"L{level}"},
                "children": [make_chain(level + 1, total)] if level < total else [],
            }

        raw = json.dumps({"children": [{"properties": {"Id": "wnd[0]"}, "children": [make_chain(1, 5)]}]})

        # Full descent walks all 5 levels
        full = parse_get_object_tree_json(raw, max_depth=200)
        assert len(full) == 1
        assert full[0].id == "L1"
        cur = full[0]
        for expected_id in ("L2", "L3", "L4", "L5"):
            assert len(cur.children) == 1
            cur = cur.children[0]
            assert cur.id == expected_id
        assert cur.children == []

        # max_depth=2 keeps L1 and L2 only
        capped = parse_get_object_tree_json(raw, max_depth=2)
        assert len(capped) == 1
        assert capped[0].id == "L1"
        assert len(capped[0].children) == 1
        assert capped[0].children[0].id == "L2"
        assert capped[0].children[0].children == []


# ---------------------------------------------------------------------------
# parse_get_object_tree_json — against real SAP-captured fixture
# ---------------------------------------------------------------------------


class TestParseGetObjectTreeJsonRealFixture:
    @pytest.fixture
    def real_json(self) -> str:
        if not BP_FULL.exists():
            pytest.skip(f"Fixture {BP_FULL} not found")
        return BP_FULL.read_text(encoding="utf-8")

    def test_parses_without_error(self, real_json):
        result = parse_get_object_tree_json(real_json, max_depth=200)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_total_element_count_matches_probe(self, real_json):
        """The probe captured a 277-element tree (wnd[0] + 276 descendants).
        We return only the descendants, so the count must be 276.
        """
        result = parse_get_object_tree_json(real_json, max_depth=200)

        def count(elems):
            n = 0
            for e in elems:
                n += 1 + count(e.children)
            return n

        assert count(result) == 276

    def test_first_element_is_menubar(self, real_json):
        """The BP create screen's first child of wnd[0] is the menubar."""
        result = parse_get_object_tree_json(real_json, max_depth=200)
        first = result[0]
        assert first.type == "GuiMenubar"
        assert first.id == "/app/con[0]/ses[0]/wnd[0]/mbar"
        assert first.type_as_number == 111
        assert first.container_type is True
        # Bools that came back as empty string in raw SAP output coerce to False
        assert first.modified is False
        assert first.is_symbol_font is False

    def test_all_top_level_windows_present(self, real_json):
        """A SAP main window has 6 standard children: mbar, tbar[0], tbar[1],
        titl, usr, sbar (any order)."""
        result = parse_get_object_tree_json(real_json, max_depth=200)
        names = {elem.name for elem in result}
        # The mbar/usr/sbar etc. names — verify the basic SAP layout came through
        assert "mbar" in names
        assert "usr" in names

    def test_max_depth_1_returns_top_level_only(self, real_json):
        """max_depth=1 means: top-level children only, no grandchildren."""
        result = parse_get_object_tree_json(real_json, max_depth=1)
        for elem in result:
            assert elem.children == []

    def test_returns_element_info_instances(self, real_json):
        result = parse_get_object_tree_json(real_json, max_depth=200)
        for elem in result:
            assert isinstance(elem, ElementInfo)
            assert isinstance(elem.children, list)
            for child in elem.children:
                assert isinstance(child, ElementInfo)


# ---------------------------------------------------------------------------
# _find_session_com — session resolution from any element
# ---------------------------------------------------------------------------


class TestFindSessionCom:
    """``_find_session_com`` walks ``.Parent`` until ``Type == "GuiSession"``.

    This mirrors the real SAP COM tree shape: every element has a Parent
    chain leading to GuiSession, and GuiSession itself has Parent =
    GuiConnection (or GuiApplication / None at the top). We deliberately
    do NOT use FindById on the element because FindById resolves IDs
    relatively to the element it is called on, not absolutely from the
    application root — see the docstring on _find_session_com.
    """

    def _make_chain(self, types: list[str]) -> MagicMock:
        """Build a Parent chain from leaf -> root, return the leaf mock."""
        parent = None
        for type_name in types:
            mock = MagicMock(name=f"com_{type_name}")
            mock.Type = type_name
            mock.Parent = parent
            parent = mock
        return parent  # the LAST one constructed is the leaf (top of chain)

    def test_finds_session_from_window_directly(self):
        """Window's parent is the session — one hop."""
        leaf = self._make_chain(["GuiSession", "GuiMainWindow"])
        # leaf is GuiMainWindow, leaf.Parent is GuiSession
        result = _find_session_com(leaf)
        assert result is leaf.Parent
        assert str(result.Type) == "GuiSession"

    def test_finds_session_from_deeply_nested_element(self):
        """Realistic 5-deep chain: usr area -> sub container -> table -> cell -> field."""
        leaf = self._make_chain(
            [
                "GuiSession",
                "GuiMainWindow",
                "GuiUserArea",
                "GuiContainerShell",
                "GuiTableControl",
                "GuiTextField",
            ]
        )
        # leaf is GuiTextField; needs to walk up 5 hops to find GuiSession
        result = _find_session_com(leaf)
        assert result is not None
        assert str(result.Type) == "GuiSession"

    def test_returns_session_directly_when_passed_a_session(self):
        """If the input IS already a GuiSession, return it without walking."""
        sess = MagicMock(name="already_a_session")
        sess.Type = "GuiSession"
        result = _find_session_com(sess)
        assert result is sess

    def test_returns_none_when_type_read_raises(self):
        """A stale COM proxy that can't even report its Type returns None."""
        elem = MagicMock()
        type(elem).Type = property(lambda self: (_ for _ in ()).throw(RuntimeError("dead proxy")))
        result = _find_session_com(elem)
        assert result is None

    def test_returns_none_when_parent_chain_terminates_without_session(self):
        """If the chain ends at GuiApplication (Parent=None) without a session,
        return None — should not raise."""
        leaf = self._make_chain(["GuiApplication", "GuiConnection"])  # no session in chain
        # leaf is GuiConnection; its Parent is GuiApplication; that Parent is None.
        result = _find_session_com(leaf)
        assert result is None

    def test_returns_none_when_parent_access_raises(self):
        """If reading .Parent raises mid-walk, return None."""
        leaf = MagicMock()
        leaf.Type = "GuiMainWindow"
        type(leaf).Parent = property(lambda self: (_ for _ in ()).throw(RuntimeError("dead")))
        result = _find_session_com(leaf)
        assert result is None

    def test_safety_limit_caps_walk_depth(self):
        """A pathological infinite Parent loop terminates at MAX_PARENT_WALK
        and uses no more than that many Type reads.

        Reviewer M2: the loose form of this test (just `result is None`)
        would pass even if MAX_PARENT_WALK was raised to 100k, in which
        case a real cycle would burn 100k COM hops before falling back.
        Asserting on the hop counter pins the safety cap as a contract.
        """
        from sapsucker.components.base import _MAX_PARENT_WALK  # pylint: disable=import-outside-toplevel

        # Counting wrapper around Type so we can verify the walk terminates
        # in bounded time. Each iteration of _find_session_com reads .Type
        # exactly once, so the count tells us how many hops the walk took.
        type_reads = [0]

        class _CountingMock:
            @property
            def Type(self) -> str:
                type_reads[0] += 1
                return "GuiVContainer"

            @property
            def Parent(self) -> "_CountingMock":
                return self  # cycle: parent is self

        result = _find_session_com(_CountingMock())
        assert result is None
        # The walk must terminate within _MAX_PARENT_WALK hops, NOT 100k or
        # whatever a regression might raise the cap to.
        assert type_reads[0] <= _MAX_PARENT_WALK, (
            f"_find_session_com walked {type_reads[0]} hops on a cycle; "
            f"the safety cap should bound it to {_MAX_PARENT_WALK}."
        )
        # Also assert it actually USED the cap (not e.g. early-returned at hop 1
        # for some other reason).
        assert type_reads[0] == _MAX_PARENT_WALK, (
            f"Expected the walk to hit the safety cap exactly ({_MAX_PARENT_WALK}); "
            f"got {type_reads[0]} hops. Either the cap changed or the walk has a "
            f"different early-exit condition than the test assumes."
        )


# ---------------------------------------------------------------------------
# GuiVContainer.dump_tree — fast-path-with-fallback integration
# ---------------------------------------------------------------------------


def _make_session_mock_with_get_object_tree(json_response: str) -> MagicMock:
    """Build a mock GuiSession that returns *json_response* from GetObjectTree."""
    session = MagicMock(name="session_com")
    session.Type = "GuiSession"
    session.Parent = None  # session is its own root for our purposes
    session.GetObjectTree = MagicMock(return_value=json_response)
    return session


def _wire_parent_to_session(elem_mock: MagicMock, session_mock: MagicMock) -> None:
    """Wire elem.Parent → session_mock so _find_session_com walks one hop."""
    # The mock from make_mock_com defaults Parent to None and Type is unset.
    # We need Type so _find_session_com can identify the leaf as not-a-session,
    # and Parent so it can walk up to the session.
    elem_mock.Type = "GuiMainWindow"
    elem_mock.Parent = session_mock


class TestDumpTreeFastPath:
    """The fast-path branch fires when GetObjectTree returns valid JSON."""

    def test_uses_fast_path_when_json_is_valid(self, caplog):
        """Real captured fixture, mocked through the COM hierarchy."""
        json_response = BP_FULL.read_text(encoding="utf-8")
        session_mock = _make_session_mock_with_get_object_tree(json_response)

        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]",
        )
        _wire_parent_to_session(parent, session_mock)
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            result = vc.dump_tree()

        # Real fixture has 276 descendants of wnd[0]
        def count(elems):
            n = 0
            for e in elems:
                n += 1 + count(e.children)
            return n

        assert count(result) == 276
        assert isinstance(result[0], ElementInfo)

        # GetObjectTree should have been called exactly once
        session_mock.GetObjectTree.assert_called_once()
        call = session_mock.GetObjectTree.call_args
        assert call.args[0] == "/app/con[0]/ses[0]/wnd[0]"
        assert set(call.args[1]) == set(DUMP_TREE_PROPS)

        # The perf log should report path=fast
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "fast"
        assert rec.elements == 276
        assert rec.container_id == "/app/con[0]/ses[0]/wnd[0]"

    def test_falls_back_when_get_object_tree_raises(self, caplog):
        """A GetObjectTree exception must trigger the per-property fallback."""
        session_mock = MagicMock(name="session_com")
        session_mock.Type = "GuiSession"  # so _find_session_com identifies it
        session_mock.Parent = None  # session is at the top of our mock chain
        session_mock.GetObjectTree = MagicMock(side_effect=RuntimeError("simulated SAP Note 3674808 crash"))

        # Set up a 1-child tree so the fallback _dump_tree_recursive returns
        # something we can verify.
        child = make_mock_com(type_as_number=31, id="c1", name="txtA")
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]/usr",
            children=[child],
        )
        _wire_parent_to_session(parent, session_mock)
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            result = vc.dump_tree()

        # Fallback succeeded, returns the child
        assert len(result) == 1
        assert result[0].id == "c1"

        # GetObjectTree was attempted once and raised
        session_mock.GetObjectTree.assert_called_once()

        # The perf log should report path=slow
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"
        assert rec.elements == 1

    def test_falls_back_when_session_cannot_be_resolved(self, caplog):
        """No GuiSession reachable -> falls back to per-property path.

        ``make_mock_com`` defaults ``Parent`` to ``None``, so
        ``_find_session_com``'s Parent walk terminates immediately
        without finding a ``Type == "GuiSession"`` element. The fast
        path raises "could not locate GuiSession" → fallback fires.
        """
        child = make_mock_com(type_as_number=31, id="c1", name="txtA")
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]/usr",
            children=[child],
        )
        # parent.Parent is None by default in make_mock_com — that alone
        # makes _find_session_com return None.
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            result = vc.dump_tree()

        assert len(result) == 1
        assert result[0].id == "c1"

        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"

    def test_falls_back_when_json_is_garbage(self, caplog):
        """Unparseable JSON (e.g. SAP returns nonsense) -> fallback."""
        session_mock = _make_session_mock_with_get_object_tree("not json at all")

        child = make_mock_com(type_as_number=31, id="c1", name="txtA")
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]/usr",
            children=[child],
        )
        _wire_parent_to_session(parent, session_mock)
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            result = vc.dump_tree()

        assert len(result) == 1
        assert result[0].id == "c1"

        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"

    def test_fast_path_permanently_disabled_on_attribute_error(self, caplog):
        """Reviewer I4: AttributeError on GetObjectTree must permanently
        disable the fast path for this process so subsequent calls skip
        the doomed attempt and go straight to the slow path.
        """
        _reset_fast_path_cache()  # ensure clean starting state

        session_mock = MagicMock(name="session_com")
        session_mock.Type = "GuiSession"
        session_mock.Parent = None
        # Simulate "GetObjectTree method does not exist on this SAP version"
        # — exactly what an SAP GUI < 7.70 PL3 install would raise.
        session_mock.GetObjectTree = MagicMock(side_effect=AttributeError("GetObjectTree"))

        child = make_mock_com(type_as_number=31, id="c1", name="txtA")
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]/usr",
            children=[child],
        )
        _wire_parent_to_session(parent, session_mock)
        vc = GuiVContainer(parent)

        try:
            # FIRST call: fast path attempted, fails with AttributeError,
            # gets cached as permanently disabled, falls back to slow path.
            with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
                result1 = vc.dump_tree()
            assert len(result1) == 1
            session_mock.GetObjectTree.assert_called_once()
            assert _base_module._fast_path_permanently_disabled is True

            # SECOND call: cache is set, fast path skipped entirely.
            # GetObjectTree must NOT be called a second time — that's the
            # point of the cache.
            session_mock.GetObjectTree.reset_mock()
            with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
                result2 = vc.dump_tree()
            assert len(result2) == 1
            session_mock.GetObjectTree.assert_not_called()

            # The perf log for the second call should still report path=slow
            slow_records = [r for r in caplog.records if r.message == "dump_tree" and r.path == "slow"]
            assert len(slow_records) >= 2  # both calls logged path=slow
        finally:
            _reset_fast_path_cache()  # don't leak state to other tests

    def test_transient_failures_do_NOT_disable_fast_path(self, caplog):
        """Reviewer I4: a transient error (e.g. RuntimeError simulating the
        SAP Note 3674808 crash bug) should NOT permanently disable the
        fast path. The next call must retry. Conservative semantics:
        only AttributeError disables.
        """
        _reset_fast_path_cache()

        session_mock = MagicMock(name="session_com")
        session_mock.Type = "GuiSession"
        session_mock.Parent = None
        # RuntimeError = transient (per _is_permanent_fast_path_failure heuristic)
        session_mock.GetObjectTree = MagicMock(side_effect=RuntimeError("flaky SAP"))

        child = make_mock_com(type_as_number=31, id="c1", name="txtA")
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]/usr",
            children=[child],
        )
        _wire_parent_to_session(parent, session_mock)
        vc = GuiVContainer(parent)

        try:
            # First call: fast path raises RuntimeError → fall back, but do NOT cache.
            with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
                vc.dump_tree()
            assert _base_module._fast_path_permanently_disabled is False

            # Second call: fast path attempted AGAIN (it's a transient failure).
            session_mock.GetObjectTree.reset_mock()
            with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
                vc.dump_tree()
            session_mock.GetObjectTree.assert_called_once()
            assert _base_module._fast_path_permanently_disabled is False
        finally:
            _reset_fast_path_cache()

    def test_is_permanent_fast_path_failure_classification(self):
        """Verify the heuristic that decides which exception classes are
        permanent vs transient. AttributeError → permanent (method missing),
        everything else → transient.
        """
        assert _is_permanent_fast_path_failure(AttributeError("GetObjectTree")) is True
        assert _is_permanent_fast_path_failure(RuntimeError("flaky")) is False
        assert _is_permanent_fast_path_failure(ValueError("bad json")) is False
        assert _is_permanent_fast_path_failure(TimeoutError("network")) is False

    def test_max_depth_respected_in_fast_path(self, caplog):
        """The fast path must honor the max_depth argument like the slow path does."""
        json_response = BP_FULL.read_text(encoding="utf-8")
        session_mock = _make_session_mock_with_get_object_tree(json_response)

        parent = make_mock_com(container_type=True, id="/app/con[0]/ses[0]/wnd[0]")
        _wire_parent_to_session(parent, session_mock)
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            shallow = vc.dump_tree(max_depth=1)

        # max_depth=1 -> top-level children only, no grandchildren
        for elem in shallow:
            assert elem.children == []

        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "fast"
        assert rec.depth_reached == 1
        assert rec.max_depth_param == 1
