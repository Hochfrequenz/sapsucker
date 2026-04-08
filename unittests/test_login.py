"""Unit tests for SAP GUI desktop login/logoff helpers."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from sapsucker._errors import SapConnectionError, SapGuiTimeoutError
from sapsucker.login import (
    _FALLBACK_SAPLOGON_PATH,
    _handle_multiple_logon_popup,
    cleanup_ghost_connections,
    close_connections_named,
    discover_saplogon_path,
    login,
    logoff,
    wait_for_session,
)


def _make_mock_session(
    program: str = "SAPMSYST",
    sbar_text: str = "",
    message_type: str = "",
    client: str = "100",
    user: str = "TESTUSER",
) -> MagicMock:
    """Create a mock GuiSession with info and find_by_id support."""
    session = MagicMock()
    session.info.program = program
    session.info.client = client
    session.info.user = user

    fields: dict[str, MagicMock] = {}

    def find_by_id(element_id: str, raise_error: bool = True) -> MagicMock | None:
        if element_id not in fields:
            if raise_error:
                raise Exception(f"Element {element_id} not found")
            return None
        return fields[element_id]

    session.find_by_id = find_by_id

    # Standard login fields
    for fid in [
        "wnd[0]/usr/txtRSYST-MANDT",
        "wnd[0]/usr/txtRSYST-BNAME",
        "wnd[0]/usr/pwdRSYST-BCODE",
        "wnd[0]/usr/txtRSYST-LANGU",
    ]:
        fields[fid] = MagicMock()

    # Main window
    wnd0 = MagicMock()
    fields["wnd[0]"] = wnd0

    # Status bar
    sbar = MagicMock()
    sbar.text = sbar_text
    sbar.message_type = message_type
    fields["wnd[0]/sbar"] = sbar

    session._fields = fields
    return session


@pytest.mark.skipif(sys.platform != "win32", reason="winreg only available on Windows")
class TestDiscoverSaplogonPath:
    """Tests for discover_saplogon_path()."""

    def test_reads_path_from_registry(self):
        """Returns saplogon.exe path from the SAPsysdir registry value."""
        import winreg  # pylint: disable=import-outside-toplevel

        mock_key = MagicMock()
        mock_key.__enter__ = lambda s: mock_key
        mock_key.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(winreg, "OpenKey", return_value=mock_key) as mock_open,
            patch.object(winreg, "QueryValueEx", return_value=(r"D:\SAP\FrontEnd\SAPGUI", 1)),
        ):
            result = discover_saplogon_path()

        assert result == r"D:\SAP\FrontEnd\SAPGUI\saplogon.exe"
        mock_open.assert_called_once_with(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SAP\SAP Shared")

    def test_falls_back_when_registry_missing(self):
        """Returns fallback path when registry key does not exist."""
        import winreg  # pylint: disable=import-outside-toplevel

        with patch.object(winreg, "OpenKey", side_effect=OSError("Key not found")):
            result = discover_saplogon_path()

        assert result == _FALLBACK_SAPLOGON_PATH


class TestLogin:
    """Tests for login()."""

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_happy_path(self, mock_time, mock_sap_gui_cls, mock_wait):
        """login() fills credentials, presses Enter, and returns session."""
        session = _make_mock_session(program="SAPMSYST")
        mock_wait.return_value = session

        # After pressing Enter, program changes (simulated by side_effect)
        call_count = 0

        def program_side_effect():
            nonlocal call_count
            call_count += 1
            # First call: SAPMSYST (triggers login), second+: SAPLSMTR_NAVIGATION
            if call_count <= 1:
                return "SAPMSYST"
            return "SAPLSMTR_NAVIGATION"

        type(session.info).program = property(lambda self: program_side_effect())

        result = login(
            connection_name="TEST",
            client="100",
            user="TESTUSER",
            password="secret",
            language="EN",
        )

        assert result is session
        mock_sap_gui_cls.connect.assert_called_once()
        session._fields["wnd[0]"].send_v_key.assert_called_once_with(0)

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_handles_multiple_logon_popup(self, mock_time, mock_sap_gui_cls, mock_wait):
        """login() handles the multiple logon popup by selecting OPT2."""
        session = _make_mock_session(program="SAPMSYST")
        mock_wait.return_value = session

        # Add popup window and OPT2 radio button
        popup = MagicMock()
        opt2 = MagicMock()
        session._fields["wnd[1]"] = popup
        session._fields["wnd[1]/usr/radMULTI_LOGON_OPT2"] = opt2

        call_count = 0

        def program_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return "SAPMSYST"
            return "SAPLSMTR_NAVIGATION"

        type(session.info).program = property(lambda self: program_side_effect())

        login(
            connection_name="TEST",
            client="100",
            user="TESTUSER",
            password="secret",
        )

        assert opt2.selected is True
        # send_v_key is called twice: once by _dismiss_system_message_popups (which
        # tries to dismiss it, sees it persists, and stops), then once by
        # _handle_multiple_logon_popup after selecting OPT2.
        assert popup.send_v_key.call_count == 2

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_raises_on_bad_credentials(self, mock_time, mock_sap_gui_cls, mock_wait):
        """login() raises SapConnectionError when login screen stays after Enter."""
        session = _make_mock_session(program="SAPMSYST", sbar_text="Login failed: bad password")
        mock_wait.return_value = session
        # Program stays SAPMSYST (login screen) after pressing Enter
        type(session.info).program = property(lambda self: "SAPMSYST")

        with pytest.raises(SapConnectionError, match="Login failed"):
            login(
                connection_name="TEST",
                client="100",
                user="TESTUSER",
                password="wrong",
            )

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_launches_sap_gui_when_connect_fails(self, mock_time, mock_sap_gui_cls, mock_wait):
        """login() launches SAP GUI when connect() raises SapConnectionError."""
        mock_sap_gui_cls.connect.side_effect = SapConnectionError("Not running")
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION")
        mock_wait.return_value = session

        login(
            connection_name="TEST",
            client="100",
            user="TESTUSER",
            password="secret",
        )

        mock_sap_gui_cls.launch.assert_called_once()


class TestLoginDoesNotCloseExistingConnections:
    """Regression guard for the parallel-multi-mandant topology.

    sapsucker 0.5.0 made ``login()`` implicitly call
    :func:`close_connections_named` before opening a new connection.
    That broke the legitimate use case of being logged into one SAP
    Logon entry as multiple distinct ``(client, user)`` tuples
    concurrently — calling ``login()`` for the second tuple would have
    closed the first. 0.5.1 reverted that. These tests pin down the
    revert: ``login()`` must NOT touch any existing connection.
    Consumers that genuinely want serial-switching are expected to call
    ``close_connections_named()`` themselves before ``login()``.
    """

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.login.close_connections_named")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_login_does_not_call_close_connections_named(self, mock_time, mock_sap_gui_cls, mock_close, mock_wait):
        """login() must NOT auto-close existing connections — that would kill parallel logins."""
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION", client="100", user="TESTUSER")
        mock_wait.return_value = session

        login(
            connection_name="HF S/4",
            client="100",
            user="TESTUSER",
            password="secret",
        )

        mock_close.assert_not_called()

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.login.close_connections_named")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_login_does_not_call_close_on_launch_path_either(self, mock_time, mock_sap_gui_cls, mock_close, mock_wait):
        """Same guard for the SAP-GUI-not-running cold-start path."""
        mock_sap_gui_cls.connect.side_effect = SapConnectionError("Not running")
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION", client="100", user="TESTUSER")
        mock_wait.return_value = session

        login(
            connection_name="HF S/4",
            client="100",
            user="TESTUSER",
            password="secret",
        )

        mock_close.assert_not_called()


class TestLoginVerifiesClientAndUser:
    """Regression coverage for issue #24.

    After the SAPMSYST guard, ``login()`` must verify the resulting session
    is actually in the requested client/user and raise ``SapConnectionError``
    on mismatch instead of silently returning a wrong-Mandant session.
    """

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_client_match_succeeds(self, mock_time, mock_sap_gui_cls, mock_wait):
        """Happy path: actual client matches requested → session returned."""
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION", client="210", user="MUSTERFRAUM")
        mock_wait.return_value = session

        result = login(
            connection_name="HF S/4",
            client="210",
            user="MUSTERFRAUM",
            password="secret",
        )

        assert result is session

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_client_mismatch_raises(self, mock_time, mock_sap_gui_cls, mock_wait):
        """If the new session lands in the wrong client, raise SapConnectionError."""
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION", client="100", user="MUSTERFRAUM")
        mock_wait.return_value = session

        with pytest.raises(SapConnectionError, match="Login landed in client '100' but '210' was requested"):
            login(
                connection_name="HF S/4",
                client="210",
                user="MUSTERFRAUM",
                password="secret",
            )

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_user_mismatch_raises(self, mock_time, mock_sap_gui_cls, mock_wait):
        """If the new session lands as the wrong user, raise SapConnectionError."""
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION", client="100", user="MUSTERMANNM")
        mock_wait.return_value = session

        with pytest.raises(
            SapConnectionError, match="Login landed as user 'MUSTERMANNM' but 'MUSTERFRAUM' was requested"
        ):
            login(
                connection_name="HF S/4",
                client="100",
                user="MUSTERFRAUM",
                password="secret",
            )

    @patch("sapsucker.login.wait_for_session")
    @patch("sapsucker.SapGui")
    @patch("sapsucker.login.time")
    def test_user_comparison_is_case_insensitive(self, mock_time, mock_sap_gui_cls, mock_wait):
        """SAP is case-insensitive about usernames — 'mustermannm' matches 'MUSTERMANNM'."""
        session = _make_mock_session(program="SAPLSMTR_NAVIGATION", client="100", user="MUSTERMANNM")
        mock_wait.return_value = session

        # Lower-case request should succeed against upper-case server response
        result = login(
            connection_name="HF S/4",
            client="100",
            user="mustermannm",
            password="secret",
        )

        assert result is session


class TestCloseConnectionsNamedPublicAPI:
    """close_connections_named must be exported as public API."""

    def test_is_exported_in_all(self):
        """Regression guard: the helper is exported so consumers can call it directly.

        Removing it from ``__all__`` while leaving the function defined
        would silently break consumers that did ``from sapsucker.login import *``
        or relied on the documented public surface.
        """
        import sapsucker.login as login_mod  # pylint: disable=import-outside-toplevel

        assert "close_connections_named" in login_mod.__all__


class TestCloseConnectionsNamed:
    """Direct unit tests for the close_connections_named helper."""

    def _fake_app(self, descriptions: list[str]) -> MagicMock:
        """Build a mock app whose ``com.Children`` mimics the SAP GUI COM collection."""
        app = MagicMock()
        children = MagicMock()
        children.Count = len(descriptions)
        conns: list[MagicMock] = []
        for desc in descriptions:
            conn = MagicMock()
            conn.Description = desc
            conns.append(conn)
        children.side_effect = lambda i: conns[i]
        app.com.Children = children
        app._mock_conns = conns  # type: ignore[attr-defined]
        return app

    def test_closes_only_matching_connection(self):
        """Entries with a different Description are left alone."""
        app = self._fake_app(["HF S/4", "HFR3", "HF S/4"])

        closed = close_connections_named(app, "HF S/4")

        assert closed == 2
        app._mock_conns[0].CloseConnection.assert_called_once()
        app._mock_conns[1].CloseConnection.assert_not_called()
        app._mock_conns[2].CloseConnection.assert_called_once()

    def test_closes_zero_when_no_match(self):
        app = self._fake_app(["HFR3", "OTHER"])

        closed = close_connections_named(app, "HF S/4")

        assert closed == 0
        for conn in app._mock_conns:
            conn.CloseConnection.assert_not_called()

    def test_iterates_in_reverse(self):
        """Reverse iteration is required because Children mutates on close.

        Asserting access order catches a refactor that switches to forward
        iteration — which would skip entries when the collection shrinks.
        """
        app = self._fake_app(["HF S/4", "HF S/4", "HF S/4"])
        access_order: list[int] = []
        app.com.Children.side_effect = lambda i: (access_order.append(i), app._mock_conns[i])[1]

        close_connections_named(app, "HF S/4")

        assert access_order == [2, 1, 0]

    def test_close_failure_is_swallowed(self):
        """A single failing CloseConnection must not abort the loop or raise."""
        app = self._fake_app(["HF S/4", "HF S/4"])
        app._mock_conns[1].CloseConnection.side_effect = RuntimeError("COM blew up")

        closed = close_connections_named(app, "HF S/4")

        # Reverse iteration hits index 1 first — its close raises and is swallowed.
        # Index 0 still gets closed successfully, so the return value is 1.
        assert closed == 1
        app._mock_conns[0].CloseConnection.assert_called_once()

    def test_children_access_failure_returns_zero(self):
        """If ``app.com.Children`` itself blows up, return 0 without raising."""
        app = MagicMock()
        type(app.com).Children = property(lambda self: (_ for _ in ()).throw(RuntimeError("no COM")))

        assert close_connections_named(app, "HF S/4") == 0


class TestLogoff:
    """Tests for logoff()."""

    @patch("sapsucker.login.cleanup_ghost_connections")
    def test_closes_parent_connection(self, mock_cleanup):
        """logoff() closes the parent connection via CloseConnection()."""
        session = MagicMock()
        logoff(session)
        session.com.Parent.CloseConnection.assert_called_once()
        mock_cleanup.assert_called_once()

    @patch("sapsucker.login.cleanup_ghost_connections")
    def test_no_fallback_to_nex(self, mock_cleanup):
        """logoff() does NOT fall back to /nEX — it just swallows the error."""
        session = MagicMock()
        session.com.Parent.CloseConnection.side_effect = Exception("COM error")
        logoff(session)
        session.send_command.assert_not_called()
        mock_cleanup.assert_called_once()

    @patch("sapsucker.login.cleanup_ghost_connections")
    def test_handles_already_closed_session(self, mock_cleanup):
        """logoff() does not raise when session is already closed."""
        session = MagicMock()
        session.com.Parent.CloseConnection.side_effect = Exception("Closed")
        logoff(session)  # Should not raise
        mock_cleanup.assert_called_once()


class TestCleanupGhostConnections:
    """Tests for cleanup_ghost_connections()."""

    @patch("sapsucker.SapGui")
    def test_closes_ghost_connections(self, mock_sap_gui_cls):
        """Ghost connections (0 sessions) are closed."""
        ghost_conn = MagicMock()
        ghost_conn.Children.Count = 0

        app = mock_sap_gui_cls.connect.return_value
        app.com.Children.Count = 1
        app.com.Children.side_effect = lambda i: ghost_conn

        cleanup_ghost_connections()
        ghost_conn.CloseConnection.assert_called_once()

    @patch("sapsucker.SapGui")
    def test_keeps_healthy_connections(self, mock_sap_gui_cls):
        """Connections with sessions are NOT closed."""
        healthy_conn = MagicMock()
        healthy_conn.Children.Count = 1

        app = mock_sap_gui_cls.connect.return_value
        app.com.Children.Count = 1
        app.com.Children.side_effect = lambda i: healthy_conn

        cleanup_ghost_connections()
        healthy_conn.CloseConnection.assert_not_called()

    @patch("sapsucker.SapGui")
    def test_sap_gui_not_running_returns_silently(self, mock_sap_gui_cls):
        """When SAP GUI is not running, cleanup returns without error."""
        mock_sap_gui_cls.connect.side_effect = Exception("Not running")
        cleanup_ghost_connections()  # Should not raise


class TestHandleMultipleLogonPopup:
    """Tests for _handle_multiple_logon_popup()."""

    def test_selects_opt2_and_sends_enter(self):
        """_handle_multiple_logon_popup() selects OPT2 and presses Enter."""
        session = MagicMock()
        popup = MagicMock()
        opt2 = MagicMock()

        def find_by_id(element_id: str, raise_error: bool = True):
            if element_id == "wnd[1]":
                return popup
            if element_id == "wnd[1]/usr/radMULTI_LOGON_OPT2":
                return opt2
            return None

        session.find_by_id = find_by_id

        with patch("sapsucker.login.time"):
            _handle_multiple_logon_popup(session)

        assert opt2.selected is True
        popup.send_v_key.assert_called_once_with(0)

    def test_does_nothing_when_no_popup(self):
        """_handle_multiple_logon_popup() returns silently when no popup exists."""
        session = MagicMock()
        session.find_by_id.return_value = None

        _handle_multiple_logon_popup(session)

        # No send_v_key should be called — the session mock's find_by_id
        # returned None, so the function should return early


class TestWaitForSession:
    """Tests for wait_for_session()."""

    def test_returns_first_session(self):
        """wait_for_session() returns the first child when it's a GuiSession."""
        from sapsucker.components.session import GuiSession as GuiSessionCls

        conn = MagicMock()
        mock_session = MagicMock(spec=GuiSessionCls)

        conn.children.__len__ = lambda self: 1
        conn.children.__getitem__ = lambda self, i: mock_session

        result = wait_for_session(conn, timeout=2)
        assert result is mock_session

    def test_raises_timeout(self):
        """wait_for_session() raises SapGuiTimeoutError when no session appears."""
        conn = MagicMock()
        conn.children.__len__ = lambda self: 0

        with pytest.raises(SapGuiTimeoutError, match="No session available"):
            wait_for_session(conn, timeout=1)
