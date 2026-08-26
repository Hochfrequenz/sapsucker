"""Tests for the screen_introspection example and its README counterpart.

Unlike ``test_examples_integration.py`` these run everywhere: they drive the
example with a fake session, so CI actually executes the code the README
publishes instead of only type-checking it.

The fake mirrors real SAP output as recorded in ``unittests/fixtures/
get_object_tree_*.json``: fully-qualified ids, and no ``children`` key on leaf
nodes. A fake that teaches a shape the API does not produce is worse than no
fake at all.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

from examples.sapsucker.screen_introspection import main, walk
from sapsucker._types import GuiComponentType
from sapsucker.models import ElementInfo

README = Path(__file__).parent.parent / "README.md"
EXAMPLE = Path(__file__).parent.parent / "examples" / "sapsucker" / "screen_introspection.py"

_WND = "/app/con[0]/ses[0]/wnd[0]"

_FULL_JSON = json.dumps({"children": [{"properties": {"Id": _WND, "Type": "GuiMainWindow", "Text": "Person anlegen"}}]})
_IDS_ONLY_JSON = json.dumps({"children": [{"properties": {"Id": _WND}}]})


def _tree() -> list[ElementInfo]:
    """A two-level ElementInfo tree: one container holding two leaves."""
    leaf_a = ElementInfo(
        id=f"{_WND}/usr/txtA",
        type="GuiTextField",
        type_as_number=GuiComponentType.GuiTextField,
        name="txtA",
        text="alpha",
        changeable=True,
    )
    leaf_b = ElementInfo(
        id=f"{_WND}/usr/txtB",
        type="GuiTextField",
        type_as_number=GuiComponentType.GuiTextField,
        name="txtB",
        text="beta",
        changeable=True,
    )
    usr = ElementInfo(
        id=f"{_WND}/usr",
        type="GuiUserArea",
        type_as_number=GuiComponentType.GuiUserArea,
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
        assert f"{_WND}/usr" in out
        assert f"{_WND}/usr/txtA" in out, "walk() did not recurse into children"
        assert f"{_WND}/usr/txtB" in out, "walk() did not recurse into children"

    def test_walk_indents_by_depth(self, capsys):
        walk(_tree())
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        depth_of = {line.strip().split()[0]: len(line) - len(line.lstrip(" ")) for line in lines}
        assert depth_of[f"{_WND}/usr/txtA"] > depth_of[f"{_WND}/usr"], "child not indented deeper than parent"

    def test_walk_on_empty_list_prints_nothing(self, capsys):
        walk([])
        assert capsys.readouterr().out == ""


class TestMain:
    def _session(self) -> MagicMock:
        session = MagicMock()
        window = MagicMock()
        window.dump_tree.return_value = _tree()
        session.find_by_id.return_value = window
        session.get_object_tree.side_effect = [_FULL_JSON, _IDS_ONLY_JSON]
        return session

    def test_main_runs_and_prints_the_tree(self, capsys):
        session = self._session()
        main(session=session)
        out = capsys.readouterr().out
        assert f"{_WND}/usr/txtA" in out
        assert f"top-level element: {_WND}" in out

    def test_main_reports_the_size_of_the_ids_only_dump(self, capsys):
        """The printed number must be the payload's length, not an arbitrary count."""
        session = self._session()
        main(session=session)
        out = capsys.readouterr().out
        assert f"ids-only dump is {len(_IDS_ONLY_JSON)} characters of JSON" in out

    def test_main_queries_the_main_window_at_full_depth(self):
        session = self._session()
        main(session=session)
        session.find_by_id.assert_called_once_with("wnd[0]")
        # "full depth by default" is a README claim — pin that no max_depth is passed
        session.find_by_id.return_value.dump_tree.assert_called_once_with()

    def test_main_requests_only_the_listed_props_then_ids_only(self):
        """The README's token-budget claim: an explicit prop list, then props=None."""
        session = self._session()
        main(session=session)
        first, second = session.get_object_tree.call_args_list
        assert first.args[0] == "wnd[0]"
        assert first.kwargs["props"] == ["Id", "Type", "Text"]
        assert second.args[0] == "wnd[0]"
        assert len(second.args) == 1, "props must not be passed positionally on the ids-only call"
        assert second.kwargs.get("props") is None


class TestReadmeStaysInSync:
    """The README publishes this code; drift between the two is the bug."""

    def _readme_snippet(self) -> str:
        section = re.search(r"### Read an entire screen(.*?)\n### ", README.read_text(encoding="utf-8"), re.S)
        assert section, "README section 'Read an entire screen' not found"
        return section.group(1)

    def _readme_code_lines(self) -> list[str]:
        blocks = re.findall(r"```python\n(.*?)```", self._readme_snippet(), re.S)
        assert blocks, "README section has no python code block"
        lines = [line.strip() for block in blocks for line in block.splitlines()]
        # the example wraps the connect boilerplate in main(); everything else must survive
        skip = {
            "",
            "import json",
            "from sapsucker import SapGui",
            "app = SapGui.connect()",
            "session = app.connections[0].sessions[0]",
        }
        return [line for line in lines if line and not line.startswith("#") and line not in skip]

    def test_every_published_line_is_executed_by_the_example(self):
        """Exhaustive in the direction that matters: nothing published goes unrun."""
        example = EXAMPLE.read_text(encoding="utf-8")
        published = self._readme_code_lines()
        assert len(published) >= 6, f"README snippet looks truncated: {published}"
        for line in published:
            code = line.partition("  # ")[0].strip()
            assert code in example, f"README publishes a line the example does not run: {code}"

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
