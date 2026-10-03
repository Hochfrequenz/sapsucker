"""GuiSession and GuiSessionInfo — session-level wrappers."""

from __future__ import annotations

import re
from typing import Any

from sapsucker._wrap import wrap_com_object
from sapsucker.components.base import GuiComponent, GuiContainer

__all__ = ["GuiSession", "GuiSessionInfo", "validate_recording_filename"]

# Verified live against SAP GUI: letters/digits and at most one dot; "_", "-", " " and a
# second dot are rejected by RecordFile with "The method got an invalid argument".
_RECORD_FILENAME = re.compile(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)?")


def validate_recording_filename(filename: str) -> None:
    """Raise ``ValueError`` unless SAP GUI's ``RecordFile`` accepts ``filename``."""
    if not _RECORD_FILENAME.fullmatch(filename):
        raise ValueError(
            f"invalid recording filename {filename!r}: SAP GUI accepts only ASCII letters and digits "
            "with at most one dot (no path, underscore, hyphen or space), e.g. 'journey3.vbs'"
        )


class GuiSessionInfo:
    """Wraps the COM GuiSessionInfo object (read-only session metadata)."""

    def __init__(self, com_object: Any) -> None:
        self._com = com_object

    @property
    def system_name(self) -> str:
        """SAP system name (SID)."""
        return str(self._com.SystemName)

    @property
    def client(self) -> str:
        """SAP client number."""
        return str(self._com.Client)

    @property
    def user(self) -> str:
        """Logged-in user name."""
        return str(self._com.User)

    @property
    def language(self) -> str:
        """Session logon language."""
        return str(self._com.Language)

    @property
    def transaction(self) -> str:
        """Currently running transaction code."""
        return str(self._com.Transaction)

    @property
    def program(self) -> str:
        """Currently running ABAP program name."""
        return str(self._com.Program)

    @property
    def screen_number(self) -> int:
        """Current dynpro screen number."""
        return int(self._com.ScreenNumber)

    @property
    def application_server(self) -> str:
        """Application server host name."""
        return str(self._com.ApplicationServer)

    @property
    def response_time(self) -> int:
        """Last server response time in milliseconds."""
        return int(self._com.ResponseTime)

    @property
    def round_trips(self) -> int:
        """Number of server round trips."""
        return int(self._com.RoundTrips)

    @property
    def session_number(self) -> int:
        """Session number within the connection."""
        return int(self._com.SessionNumber)

    @property
    def system_number(self) -> int:
        """SAP system number."""
        return int(self._com.SystemNumber)

    @property
    def codepage(self) -> int:
        """Character codepage number."""
        return int(self._com.Codepage)

    @property
    def flushes(self) -> int:
        """Number of automation queue flushes."""
        return int(self._com.Flushes)

    @property
    def group(self) -> str:
        """Logon group name."""
        return str(self._com.Group)

    @property
    def message_server(self) -> str:
        """Message server host name."""
        return str(self._com.MessageServer)

    @property
    def system_session_id(self) -> str:
        """Unique system session identifier."""
        return str(self._com.SystemSessionId)

    @property
    def is_low_speed_connection(self) -> bool:
        """Whether this is a low-speed connection."""
        return bool(self._com.IsLowSpeedConnection)

    @property
    def scripting_mode_read_only(self) -> bool:
        """Whether scripting is restricted to read-only mode."""
        return bool(self._com.ScriptingModeReadOnly)

    @property
    def scripting_mode_recording_disabled(self) -> bool:
        """Whether script recording is disabled."""
        return bool(self._com.ScriptingModeRecordingDisabled)

    def __repr__(self) -> str:
        return (
            f"GuiSessionInfo(system={self._com.SystemName!r}, "
            f"client={self._com.Client!r}, "
            f"user={self._com.User!r}, "
            f"transaction={self._com.Transaction!r})"
        )


class GuiSession(GuiContainer):
    """Wraps the COM GuiSession interface (TypeAsNumber 12).

    The session is the main entry point for interacting with an SAP screen.
    """

    @property
    def info(self) -> GuiSessionInfo:
        """Return session metadata wrapped in GuiSessionInfo."""
        return GuiSessionInfo(self._com.Info)

    @property
    def busy(self) -> bool:
        """Whether the session is currently processing a server request."""
        return bool(self._com.Busy)

    @property
    def active_window(self) -> GuiComponent:
        """Return the active window wrapped in the correct Python class."""
        return wrap_com_object(self._com.ActiveWindow)

    def create_session(self) -> None:
        """Open an additional session (like /o in the OK-code field)."""
        self._com.CreateSession()

    def end_transaction(self) -> None:
        """End the current transaction (like /n in the OK-code field)."""
        self._com.EndTransaction()

    def send_command(self, command: str) -> None:
        """Execute a command string synchronously (e.g. '/nSE38')."""
        self._com.SendCommand(command)

    def send_command_async(self, command: str) -> None:
        """Execute a command string asynchronously."""
        self._com.SendCommandAsync(command)

    # --- Script recording (issue #125) ---

    def start_recording(self, filename: str) -> str:
        r"""Start recording this session's user interactions to a ``.vbs`` file.

        Drives SAP GUI's built-in recorder through the COM API
        (``ISapSessionTarget.RecordFile`` / ``Record`` - the same feature as
        Alt+F12 -> *Script Recording and Playback*). Pair it with
        :mod:`sapsucker.monitor` to capture *when* alongside *what*
        (``sapsucker-monitor --record``).

        Args:
            filename: A bare filename WITHOUT path. Verified live: ASCII
                letters and digits with at most one dot (``journey3.vbs``).
                Underscores, hyphens, spaces and a second dot are rejected by
                SAP GUI ("The method got an invalid argument"), so they are
                rejected here with a clear message. The file lands in the SAP
                GUI Scripts folder on the client (registry
                ``HKCU\SOFTWARE\SAP\SAPGUI Front\SAP Frontend
                Server\Scripting\SaveScriptTo``) and SAP GUI upper-cases the
                name. The recorder writes UTF-16.

        Returns:
            The full path SAP GUI resolved the recording to.

        Raises:
            ValueError: If ``filename`` is not a name SAP GUI accepts.
            pywintypes.com_error: If SAP GUI refuses to record. Verified live:
                an invalid file name ("The method got an invalid argument").
                NOT verified: the recording-disabled case
                (``sapgui/user_scripting_disable_recording`` = TRUE) was not
                provoked, because it needs an RZ11 change on a shared system;
                whether it fails here or silently records nothing is unknown.
                The COM error text is the only diagnostic.

        Recording-mode behaviour differences (documented in the API guide):
        the F4 help dialog is always shown as a modal window, and drag &
        drop is disabled. A journey captured this way is
        *recorded-behaviour* - label it as such when replaying.

        Do not call this while a recording is already running; the behaviour of
        changing ``RecordFile`` mid-recording is unverified.

        Stop with :meth:`stop_recording`.
        """
        validate_recording_filename(filename)
        self._com.RecordFile = filename
        resolved = str(self._com.RecordFile)
        self._com.Record = True
        return resolved

    def stop_recording(self) -> None:
        """Stop script recording started by :meth:`start_recording`."""
        self._com.Record = False

    @property
    def is_recording(self) -> bool:
        """Whether script recording is currently active on this session."""
        return bool(self._com.Record)

    def lock_session_ui(self) -> None:
        """Lock the session UI to prevent user interaction during scripting."""
        self._com.LockSessionUI()

    def unlock_session_ui(self) -> None:
        """Unlock the session UI."""
        self._com.UnlockSessionUI()

    def get_v_key_description(self, v_key: int) -> str:
        """Return a human-readable description for a virtual key number."""
        return str(self._com.GetVKeyDescription(v_key))

    def get_object_tree(self, element_id: str, props: list[str] | None = None) -> str:
        """Return the SAP GUI object tree starting from *element_id* as JSON.

        Wraps ``GuiSession.GetObjectTree`` (SAP GUI for Windows >= 7.70 PL3,
        released August 2021). Returns a JSON string containing the
        requested properties for every element in the subtree rooted at
        *element_id*. The exact JSON shape (verified empirically against a
        live SAP system, see ``unittests/fixtures/get_object_tree_*.json``):

            {
              "children": [
                {
                  "properties": {"Id": "...", "Type": "...", ...},
                  "children": [ ... recursive ... ]
                }
              ]
            }

        The queried element is wrapped one level deep in
        ``response["children"][0]``; its actual descendants are
        ``response["children"][0]["children"]``.

        When *props* is None, only the ``Id`` property is returned for
        each element. Pass an explicit list to get more — note that the
        list MUST contain only simple-typed properties (String, Integer,
        Bool); see SAP's ``GuiMagicDispIDs`` enumeration for the supported
        names.

        This is a single COM round-trip regardless of subtree size, which
        makes it dramatically cheaper than reading properties individually
        for trees of more than a handful of elements. Empirical
        measurement against a 277-element SAP screen: 86 ms for all 21
        sapsucker-known properties via this call vs. estimated ~2900 ms
        for the equivalent per-property COM reads. See
        ``GuiVContainer.dump_tree`` for the consumer.

        Args:
            element_id: Element ID to start from (e.g. ``"wnd[0]"``,
                ``"wnd[0]/usr"``).
            props: Optional list of property names to include. If None,
                only ``Id`` is returned.

        Returns:
            A JSON string. Parse with ``json.loads`` or (preferred) one
            of pydantic's ``model_validate_json`` calls.
        """
        # COM dispatch differs between "1 arg" and "2 args" call shapes,
        # so we cannot just pass ``props=None`` through.
        if props is None:
            return str(self._com.GetObjectTree(element_id))
        return str(self._com.GetObjectTree(element_id, props))
