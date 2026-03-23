"""Integration tests for the sapsucker library.

Tests are skipped unless:
- Running on Windows (COM is Windows-only)
- Running on the authorized SAP test machine (same check as WebGUI tests)
- SAP credentials are configured in .env

The login tests auto-launch SAP Logon if it's not running — no manual
startup needed. The read-only tests (test_connect_*, test_find_*) require
an existing logged-in session.
"""

import sys

import pytest

from unittests.conftest import is_sap_integration_test_machine

# Skip everything on non-Windows
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="SAP GUI COM is Windows-only")

# Skip on non-authorized machines (same guard as WebGUI integration tests)
skip_not_sap_machine = pytest.mark.skipif(
    not is_sap_integration_test_machine(),
    reason="SAP integration tests only run on authorized machines",
)


def _has_active_session() -> bool:
    """Check if SAP GUI has at least one logged-in session (for read-only tests)."""
    try:
        from sapsucker import SapGui

        app = SapGui.connect()
        if len(app.connections) == 0:
            return False
        conn = app.connections[0]
        return len(conn.children) > 0
    except Exception:
        return False


def _login_creds_configured() -> bool:
    """Check whether SAP login credentials are configured."""
    return False  # Not used in standalone sapsucker tests


# Guards for different test categories
skip_no_active_session = pytest.mark.skipif(not _has_active_session(), reason="No active SAP GUI session")


# ---------------------------------------------------------------------------
# Read-only tests (require an existing logged-in session)
# ---------------------------------------------------------------------------


def _get_session():
    """Helper: connect and return a wrapped GuiSession for the first session."""
    from sapsucker import SapGui

    app = SapGui.connect()
    conn = app.connections[0]
    return conn.children[0]


@skip_not_sap_machine
@skip_no_active_session
def test_connect_returns_gui_application():
    from sapsucker import SapGui
    from sapsucker.components.application import GuiApplication

    app = SapGui.connect()
    assert isinstance(app, GuiApplication)


@skip_not_sap_machine
@skip_no_active_session
def test_application_has_connections():
    from sapsucker import SapGui

    app = SapGui.connect()
    assert len(app.connections) > 0


@skip_not_sap_machine
@skip_no_active_session
def test_connection_has_sessions():
    from sapsucker import SapGui

    app = SapGui.connect()
    conn = app.connections[0]
    assert len(conn.children) > 0


@skip_not_sap_machine
@skip_no_active_session
def test_session_info():
    session = _get_session()
    info = session.info
    assert info.system_name != ""
    assert info.user != ""
    assert info.language != ""


@skip_not_sap_machine
@skip_no_active_session
def test_find_main_window():
    from sapsucker.components.window import GuiMainWindow

    session = _get_session()
    wnd = session.find_by_id("wnd[0]")
    assert isinstance(wnd, GuiMainWindow)


@skip_not_sap_machine
@skip_no_active_session
def test_find_statusbar():
    from sapsucker.components.statusbar import GuiStatusbar

    session = _get_session()
    sbar = session.find_by_id("wnd[0]/sbar")
    assert isinstance(sbar, GuiStatusbar)


@skip_not_sap_machine
@skip_no_active_session
def test_find_okcode_field():
    from sapsucker.components.okcode import GuiOkCodeField

    session = _get_session()
    okcode = session.find_by_id("wnd[0]/tbar[0]/okcd")
    assert isinstance(okcode, GuiOkCodeField)


@skip_not_sap_machine
@skip_no_active_session
def test_find_by_id_returns_typed_wrappers():
    from sapsucker.components.base import GuiComponent

    session = _get_session()
    elem = session.find_by_id("wnd[0]")
    assert isinstance(elem, GuiComponent)
    assert hasattr(elem, "com")


@skip_not_sap_machine
@skip_no_active_session
def test_dump_tree_on_main_window():
    from sapsucker.models import ElementInfo

    session = _get_session()
    wnd = session.find_by_id("wnd[0]")
    tree = wnd.dump_tree(max_depth=2)
    assert isinstance(tree, list)
    assert len(tree) > 0
    assert isinstance(tree[0], ElementInfo)


@skip_not_sap_machine
@skip_no_active_session
def test_read_statusbar_text():
    session = _get_session()
    sbar = session.find_by_id("wnd[0]/sbar")
    assert isinstance(sbar.text, str)
