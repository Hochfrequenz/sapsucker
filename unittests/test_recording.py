"""Tests for the .vbs recording parser (sapsucker._recording)."""

import pytest

from sapsucker._recording import Recording, RecordingParseError, RecordingStep

CORPUS = [
    "docs/spike/journey3_bp.vbs",
    "docs/spike/journey4_bp.vbs",
    "docs/spike/journey5_bp.vbs",
    "docs/spike/journey6_bp.vbs",
    "docs/spike/journey_se16n.vbs",
    "docs/spike/se16but000.vbs",
]


class TestCorpusRoundTrip:
    """Every committed recording parses and re-renders byte-identically."""

    @pytest.mark.parametrize("path", CORPUS)
    def test_round_trip(self, path):
        rec = Recording.load(path)
        original = open(path, "rb").read().decode("utf-8")
        assert rec.render() == original, f"round-trip failed for {path}"

    @pytest.mark.parametrize("path", CORPUS)
    def test_steps_nonempty(self, path):
        rec = Recording.load(path)
        assert rec.steps
        assert all(s.element_id.startswith("wnd[") for s in rec.steps)
        assert all(s.member for s in rec.steps)

    @pytest.mark.parametrize("path", CORPUS)
    def test_preamble_preserved(self, path):
        rec = Recording.load(path)
        assert rec.prologue, "header comments / preamble must be kept verbatim"
        assert rec.preamble_is_guarded


class TestStatementForms:
    """Each form the corpus exercises, plus the grammar's superset."""

    def test_bare_no_arg_method(self):
        rec = Recording.parse('session.findById("wnd[0]/tbar[1]/btn[5]").press\n')
        assert rec.steps[0] == RecordingStep(
            "wnd[0]/tbar[1]/btn[5]", "press", None, 'session.findById("wnd[0]/tbar[1]/btn[5]").press', 1
        )

    def test_unparenthesised_args(self):
        rec = Recording.parse('session.findById("wnd[0]").sendVKey 0\n')
        assert rec.steps[0].member == "sendVKey"
        assert rec.steps[0].args == ("0",)

    def test_multi_arg_unparenthesised(self):
        rec = Recording.parse('session.findById("wnd[0]").resizeWorkingPane 152,33,false\n')
        assert rec.steps[0].member == "resizeWorkingPane"
        assert rec.steps[0].args == ("152", "33", "false")

    def test_string_assignment(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtF").text = "hello"\n')
        assert rec.steps[0].member == "text"
        assert rec.steps[0].args == ("hello",)

    def test_string_with_specials(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtF").text = "a = b (c), d"\n')
        assert rec.steps[0].args == ("a = b (c), d",)

    def test_vb_escaped_quotes(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtF").text = "say ""hi"" now"\n')
        assert rec.steps[0].args == ('say "hi" now',)

    def test_parenthesised_call(self):
        """Accepted by the grammar though no committed recording uses it."""
        rec = Recording.parse('session.findById("wnd[0]/usr/grid").setCurrentCell(5, "COL")\n')
        assert rec.steps[0].member == "setCurrentCell"
        assert rec.steps[0].args == ("5", "COL")

    def test_string_arg_with_comma(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/shell").select "1,2"\n')
        assert rec.steps[0].args == ("1,2",)  # the comma inside the string did not split it

    def test_colon_chained_statements(self):
        rec = Recording.parse('session.findById("wnd[0]").setFocus: session.findById("wnd[0]").sendVKey 0\n')
        assert [s.member for s in rec.steps] == ["setFocus", "sendVKey"]

    def test_numeric_assignment(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtF").caretPosition = 14\n')
        assert rec.steps[0].args == ("14",)


class TestFailLoud:
    def test_unknown_statement_raises(self):
        with pytest.raises(RecordingParseError, match="line 1"):
            Recording.parse('session.findById("wnd[0]").frobnicate ~ 1\n')

    def test_unterminated_string(self):
        with pytest.raises(RecordingParseError, match="unterminated"):
            Recording.parse('session.findById("wnd[0]/usr/txtF").text = "oops\n')

    def test_no_statements(self):
        with pytest.raises(RecordingParseError, match="no findById"):
            Recording.parse("' just a comment\n")

    def test_missing_member(self):
        with pytest.raises(RecordingParseError, match="missing member"):
            Recording.parse('session.findById("wnd[0]").\n')


class TestElementIdGrammar:
    def test_bdt_id_with_colons(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/subA02P01:SAPLBUD0:1130/cmbBUS000FLDS-TITLE_MEDI").key = "0002"\n'
        )
        assert rec.steps[0].element_id == ("wnd[0]/usr/subA02P01:SAPLBUD0:1130/cmbBUS000FLDS-TITLE_MEDI")
        assert rec.steps[0].args == ("0002",)

    def test_table_control_id(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/sub/4/sub/4/1/txt[0,19]").text = "x"\n')
        assert rec.steps[0].element_id == "wnd[0]/usr/sub/4/sub/4/1/txt[0,19]"


class TestUtf16Loading:
    def test_utf16_le(self, tmp_path):
        tmp = tmp_path / "rec.vbs"
        tmp.write_bytes('session.findById("wnd[0]").press\n'.encode("utf-16-le"))
        rec = Recording.load(str(tmp))
        assert rec.steps[0].member == "press"
