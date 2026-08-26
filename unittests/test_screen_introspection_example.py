"""Tests for the screen_introspection example and its README counterpart.

Unlike ``test_examples_integration.py`` these run everywhere: they drive the
example with a fake session, so CI actually executes the code the README
publishes instead of only type-checking it.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

from examples.sapsucker.screen_introspection import main, walk
from sapsucker.models import ElementInfo

README = Path(__file__).parent.parent / "README.md"
EXAMPLE = Path(__file__).parent.parent / "examples" / "sapsucker" / "screen_introspection.py"


def _tree() -> list[ElementInfo]:
    """A two-level ElementInfo tree: one container holding two leaves."""
    leaf_a = ElementInfo(
        id="wnd[0]/usr/txtA", type="GuiTextField", type_as_number=31, name="txtA", text="alpha", changeable=True
    )
    leaf_b = ElementInfo(
        id="wnd[0]/usr/txtB", type="GuiTextField", type_as_number=31, name="txtB", text="beta", changeable=True
    )
    usr = ElementInfo(
        id="wnd[0]/usr",
        type="GuiUserArea",
        type_as_number=52,
        name="usr",
        text="",
        changeable=True,
        children=[leaf_a, leaf_b],
    )
    return [usr]


class TestWalk:
    def test_walk_recurses_into_nested_children(self, capsys):
        """A flat loop would print only the container — walk() must reach the leaves."""
        walk(_tree())
        out = capsys.readouterr().out
        assert "wnd[0]/usr" in out
        assert "wnd[0]/usr/txtA" in out, "walk() did not recurse into children"
        assert "wnd[0]/usr/txtB" in out, "walk() did not recurse into children"

    def test_walk_indents_by_depth(self, capsys):
        walk(_tree())
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        depth_of = {line.strip().split()[0]: len(line) - len(line.lstrip(" ")) for line in lines}
        assert depth_of["wnd[0]/usr/txtA"] > depth_of["wnd[0]/usr"], "child not indented deeper than parent"

    def test_walk_on_empty_list_prints_nothing(self, capsys):
        walk([])
        assert capsys.readouterr().out == ""


class TestMain:
    def _session(self) -> MagicMock:
        session = MagicMock()
        window = MagicMock()
        window.dump_tree.return_value = _tree()
        session.find_by_id.return_value = window
        session.get_object_tree.side_effect = [
            json.dumps(
                {"children": [{"properties": {"Id": "wnd[0]", "Type": "GuiMainWindow", "Text": "SAP"}, "children": []}]}
            ),
            json.dumps({"children": [{"properties": {"Id": "wnd[0]"}, "children": []}]}),
        ]
        return session

    def test_main_runs_and_prints_the_tree(self, capsys):
        session = self._session()
        main(session=session)
        out = capsys.readouterr().out
        assert "wnd[0]/usr/txtA" in out
        assert "top-level element: wnd[0]" in out
        assert "characters of JSON" in out

    def test_main_queries_the_main_window(self):
        session = self._session()
        main(session=session)
        session.find_by_id.assert_called_once_with("wnd[0]")

    def test_main_requests_only_the_listed_props_then_ids_only(self):
        """The README's token-budget claim: an explicit prop list, then props=None."""
        session = self._session()
        main(session=session)
        first, second = session.get_object_tree.call_args_list
        assert first.kwargs["props"] == ["Id", "Type", "Text"]
        assert "props" not in second.kwargs or second.kwargs["props"] is None


class TestReadmeStaysInSync:
    """The README publishes this code; drift between the two is the bug."""

    def _readme_snippet(self) -> str:
        section = re.search(r"### Read an entire screen(.*?)\n### ", README.read_text(encoding="utf-8"), re.S)
        assert section, "README section 'Read an entire screen' not found"
        return section.group(1)

    def test_readme_walk_body_matches_the_example(self):
        snippet = self._readme_snippet()
        example = EXAMPLE.read_text(encoding="utf-8")
        for line in [
            'print("  " * depth, element.id, element.type, element.text)',
            "walk(element.children, depth + 1)",
        ]:
            assert line in snippet, f"README lost: {line}"
            assert line in example, f"example lost: {line}"

    def test_readme_and_example_make_the_same_api_calls(self):
        snippet = self._readme_snippet()
        example = EXAMPLE.read_text(encoding="utf-8")
        for call in [
            'session.get_object_tree("wnd[0]", props=["Id", "Type", "Text"])',
            'session.get_object_tree("wnd[0]")',
            'session.find_by_id("wnd[0]")',
        ]:
            assert call in snippet, f"README lost: {call}"
            assert call in example, f"example lost: {call}"
