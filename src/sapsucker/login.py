"""SAP GUI desktop login/logoff helpers.

Lessons learned from live testing against HF S/4 (S4U, client 100):

- Each login() opens a NEW connection (con[N]) via app.open_connection().
  This is NOT the same as opening a new session/mode (/o) within an
  existing connection. Connections are independent; sessions share a login.

- The "multiple logon" popup (Lizenzinformation bei Mehrfachanmeldung)
  appears when the same user is already logged in on another connection.
  Its default radio button selection is NOT stable — it can be OPT1, OPT2,
  or OPT3 depending on server state. Always explicitly select OPT2.

- send_command("/nEX") can BLOCK indefinitely on COM when closing a session.
  Use connection.CloseConnection() instead — it returns immediately.

- SAP GUI leaves "ghost connections" (0 sessions) in the COM tree after
  closing sessions. These must be cleaned up via CloseConnection().

- The login screen is program SAPMSYST, screen 20 (standard on all systems).
  Field IDs: txtRSYST-MANDT (client), txtRSYST-BNAME (user),
  pwdRSYST-BCODE (password), txtRSYST-LANGU (language).
"""

# pylint: disable=import-outside-toplevel,broad-exception-caught,too-many-arguments,too-many-positional-arguments

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Any, cast

from sapsucker._errors import SapConnectionError, SapGuiTimeoutError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sapsucker.components.session import GuiSession

__all__ = [
    "cleanup_ghost_connections",
    "close_connections_named",
    "discover_saplogon_path",
    "login",
    "logoff",
    "wait_for_session",
]

_FALLBACK_SAPLOGON_PATH = r"C:\Program Files\SAP\FrontEnd\SAPGUI\saplogon.exe"


def discover_saplogon_path() -> str:
    """Read the SAP GUI install dir from the Windows registry, fall back to the default path."""
    if sys.platform != "win32":
        return _FALLBACK_SAPLOGON_PATH
    try:
        import winreg  # pylint: disable=import-outside-toplevel,import-error

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SAP\SAP Shared") as key:
            sap_sysdir, _ = winreg.QueryValueEx(key, "SAPsysdir")
            return rf"{sap_sysdir}\saplogon.exe"
    except OSError:
        return _FALLBACK_SAPLOGON_PATH


def login(
    connection_name: str,
    client: str,
    user: str,
    password: str,
    language: str = "EN",
    saplogon_exe_path: str | None = None,
    timeout: int = 30,
) -> GuiSession:
    """Connect to SAP GUI, open a NEW connection, fill the login screen, return a session.

    Launches SAP Logon if not already running. Handles the "multiple logon" popup
    by selecting "continue without ending other sessions" (OPT2).

    Each call to ``login()`` opens a fresh ``con[N]`` via
    ``app.open_connection`` and **does not affect any existing connection
    that already shares the same ``connection_name``**. This makes it
    legal to be logged into the same SAP Logon entry as multiple
    distinct ``(client, user)`` tuples concurrently — for example one
    background sub-agent in client 100 and another in client 210, both
    via "HF S/4". If you instead want serial-switching semantics
    (close any prior matching connection first), call
    :func:`close_connections_named` explicitly before ``login()``.

    Args:
        connection_name: SAP Logon entry name (e.g. "HF S/4").
        client: SAP client/mandant (e.g. "100").
        user: SAP username.
        password: SAP password.
        language: Login language (default "EN").
        saplogon_exe_path: Path to saplogon.exe (default: standard install path).
        timeout: Max seconds to wait for connection/session to become available.

    Returns:
        A logged-in GuiSession whose ``info.client`` and ``info.user``
        match the requested values.

    Raises:
        SapGuiTimeoutError: If SAP GUI or session doesn't become available.
        ScriptingDisabledError: If scripting is disabled on the server.
        SapConnectionError: If login fails. This covers wrong credentials,
            SAP error messages on the status bar, or the session ending up
            in a different client / as a different user than requested
            (e.g. because SSO or a cached logon ticket bypassed the
            explicit credentials — see issue #24).
    """
    from sapsucker import SapGui  # pylint: disable=import-outside-toplevel

    # Step 1: Ensure SAP GUI is running
    try:
        app = SapGui.connect()
    except SapConnectionError:
        app = SapGui.launch(exe_path=saplogon_exe_path or discover_saplogon_path(), timeout=timeout)

    # Step 2: Open a new connection. ``app.open_connection(description)``
    # adds a new ``con[N]`` to the COM tree without affecting any existing
    # connection that shares the same description — multiple parallel
    # connections to the same SAP Logon entry (e.g. one per mandant or
    # one per user) are a supported topology and the caller may rely on it.
    # If you genuinely want serial-switching semantics (close any prior
    # connection of the same name first), call :func:`close_connections_named`
    # explicitly before ``login()`` — see issue #24 history for the
    # rationale behind keeping that behaviour opt-in.
    conn = app.open_connection(connection_name, sync=True)
    session = wait_for_session(conn, timeout=timeout)

    # Step 3: Dismiss any system message popups before the login screen
    _dismiss_system_message_popups(session)

    # Step 4: Fill login screen (if we're on the login dynpro)
    # find_by_id returns GuiComponent | None but the actual runtime objects
    # expose .text, .send_v_key, etc. via their concrete subclasses.
    if session.info.program == "SAPMSYST":
        cast(Any, session.find_by_id("wnd[0]/usr/txtRSYST-MANDT")).text = client
        cast(Any, session.find_by_id("wnd[0]/usr/txtRSYST-BNAME")).text = user
        cast(Any, session.find_by_id("wnd[0]/usr/pwdRSYST-BCODE")).text = password
        cast(Any, session.find_by_id("wnd[0]/usr/txtRSYST-LANGU")).text = language
        cast(Any, session.find_by_id("wnd[0]")).send_v_key(0)  # Enter

        # Brief wait for server response
        time.sleep(1)

        # Step 5: Handle "multiple logon" popup
        _handle_multiple_logon_popup(session)

    # Step 6: Verify login succeeded
    if session.info.program == "SAPMSYST":
        sbar = cast(Any, session.find_by_id("wnd[0]/sbar"))
        raise SapConnectionError(f"Login failed: {sbar.text}")

    sbar = session.find_by_id("wnd[0]/sbar", raise_error=False)
    if sbar is not None and cast(Any, sbar).message_type == "E":
        raise SapConnectionError(f"Login failed: {cast(Any, sbar).text}")

    # Step 7: Verify we actually landed where we asked to. If the
    # SAPMSYST credential-fill block was skipped (e.g. because SSO/SNC
    # or a cached logon ticket auto-authenticated before we reached the
    # login dynpro, or because some other path bypassed it) we'd rather
    # raise loudly here than hand back a session silently logged in as
    # the wrong user or in the wrong client — see issue #24.
    actual_client = str(session.info.client)
    if actual_client != client:
        raise SapConnectionError(
            f"Login landed in client {actual_client!r} but {client!r} was requested. "
            f"This usually means the SAP Logon entry {connection_name!r} was already "
            "open in a different client and the per-call credential override did not "
            "take effect."
        )
    actual_user = str(session.info.user)
    if actual_user.upper() != user.upper():
        raise SapConnectionError(
            f"Login landed as user {actual_user!r} but {user!r} was requested. "
            "This can happen when SSO/SNC or a cached logon ticket bypasses the "
            "explicit credentials passed to login()."
        )

    logger.info(
        "desktop_login",
        extra={"connection": connection_name, "user": user, "system": connection_name},
    )
    return session


def logoff(session: GuiSession) -> None:
    """Close the session's connection, then clean up ghost connections.

    Uses CloseConnection() on the parent connection rather than /nEX,
    because send_command("/nEX") can block indefinitely on COM.
    """
    try:
        parent_conn = session.com.Parent
        parent_conn.CloseConnection()
    except Exception:
        pass  # Connection is likely already dead

    logger.info("desktop_logoff")

    # Clean up ghost connections (0 sessions) left behind
    cleanup_ghost_connections()


def close_connections_named(app: Any, description: str) -> int:
    """Close every open connection whose ``Description`` matches *description*.

    Opt-in helper for consumers that want **serial-switching** semantics
    on a SAP Logon entry: call this *before* :func:`login` to drop any
    prior connection of the same name so the upcoming ``login()`` is the
    only one. ``login()`` itself does **not** call this — it opens a new
    parallel connection without affecting existing ones, because the
    parallel-multi-mandant topology (one SAP Logon entry, multiple
    concurrent ``(client, user)`` logins) is a supported and common
    arrangement we don't want to break by default. See issue #24 for
    history.

    Returns the number of connections actually closed. If SAP GUI is
    not running (or ``app.com.Children`` is not accessible for any other
    reason), returns 0 without raising.

    Uses reverse iteration over ``app.com.Children`` because the COM
    ``GuiConnectionCollection`` mutates on close — the same reverse
    pattern used by :func:`cleanup_ghost_connections` below.
    """
    closed = 0
    try:
        children = app.com.Children
    except Exception:
        return 0

    for i in range(children.Count - 1, -1, -1):
        try:
            conn = children(i)
            if str(conn.Description) == description:
                conn.CloseConnection()
                closed += 1
        except Exception:
            pass  # Best effort — never let cleanup mask the caller's real work
    if closed:
        logger.debug("close_connections_named", extra={"description": description, "closed": closed})
    return closed


def cleanup_ghost_connections() -> None:
    """Close all connections that have 0 sessions (ghost connections).

    SAP GUI sometimes leaves dead connections in the COM tree after
    /nEX or failed connection attempts. These are harmless but clutter
    the connection list.
    """
    from sapsucker import SapGui

    try:
        app = SapGui.connect()
    except Exception:
        return  # SAP GUI not running, nothing to clean

    try:
        # Use raw COM to iterate connections — avoids type issues with wrapped collections
        raw_conns = app.com.Children
        closed = 0
        for i in range(raw_conns.Count - 1, -1, -1):
            raw_conn = raw_conns(i)
            if raw_conn.Children.Count == 0:
                try:
                    raw_conn.CloseConnection()
                    closed += 1
                except Exception:
                    pass  # Best effort
        if closed:
            logger.debug("ghost_cleanup", extra={"closed": closed})
    except Exception:
        pass  # Don't fail on cleanup


def wait_for_session(conn: Any, timeout: int = 30) -> GuiSession:
    """Wait until the connection has at least one session, then return it wrapped.

    Raises:
        SapGuiTimeoutError: If no session appears within *timeout* seconds.
    """
    from sapsucker.components.session import GuiSession as GuiSessionCls

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if len(conn.children) > 0:
                session = conn.children[0]
                if isinstance(session, GuiSessionCls):
                    return session
        except Exception:
            pass
        time.sleep(0.5)
    raise SapGuiTimeoutError(f"No session available on connection after {timeout}s")


def _dismiss_system_message_popups(session: GuiSession) -> None:
    """Dismiss system message popups that appear after opening a connection.

    SAP Basis admins can broadcast messages (e.g. maintenance windows) that
    appear as modal popups (wnd[1]) before the login screen is interactable.
    These must be closed with Enter before the login fields can be filled.

    Stops when no popup is present, or when a popup survives dismissal
    (indicating it's interactive, like the multiple-logon dialog).
    """
    for _ in range(5):  # Handle up to 5 stacked popups
        popup = session.find_by_id("wnd[1]", raise_error=False)
        if popup is None:
            return
        logger.info("system_message_popup", extra={"title": str(cast(Any, popup).text)})
        cast(Any, popup).send_v_key(0)  # Enter to dismiss
        time.sleep(0.5)
        # If popup is still there after Enter, it's not a simple system message
        if session.find_by_id("wnd[1]", raise_error=False) is not None:
            return


def _handle_multiple_logon_popup(session: GuiSession) -> None:
    """Handle the 'Lizenzinformation bei Mehrfachanmeldung' popup.

    Always explicitly selects OPT2 ('continue without ending other sessions')
    because the default selection is not stable across SAP versions.
    """
    popup = session.find_by_id("wnd[1]", raise_error=False)
    if popup is None:
        return
    logger.info("multiple_logon_popup", extra={"action": "continue_without_ending"})
    opt2 = session.find_by_id("wnd[1]/usr/radMULTI_LOGON_OPT2", raise_error=False)
    if opt2 is not None:
        cast(Any, opt2).selected = True
        cast(Any, session.find_by_id("wnd[1]")).send_v_key(0)  # Enter
        time.sleep(1)  # Wait for server to process
