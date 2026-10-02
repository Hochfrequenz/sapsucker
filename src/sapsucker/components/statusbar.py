"""GuiStatusbar and GuiStatusPane — status bar components."""

from __future__ import annotations

from sapsucker.components.base import GuiVComponent

__all__ = ["GuiStatusPane", "GuiStatusbar", "GuiVHViewSwitch"]


class GuiStatusbar(GuiVComponent):
    """Wraps the COM GuiStatusbar interface (TypeAsNumber 103).

    The status bar at the bottom of the SAP GUI window.
    Note: extends GuiVComponent, NOT GuiVContainer.
    """

    @property
    def message_type(self) -> str:
        """Message type character (S, W, E, A, I). Observed ``""`` on an empty bar."""
        return str(self._com.MessageType)

    @property
    def message_id(self) -> str:
        """Message class (T100 ``ARBGB``) of the current message, e.g. ``"DS"``.

        The COM value is space-padded to 20 characters (observed ``"DS" + 18 spaces``);
        trailing spaces are stripped here. Observed ``""`` on an empty bar.

        Not checked: a status-bar text with no T100 message behind it.
        """
        return str(self._com.MessageId).rstrip(" ")

    @property
    def message_number(self) -> str:
        """Message number within the message class, as the zero-padded string SAP GUI returns.

        Observed ``"017"`` and ``"343"``; ``""`` on an empty bar.
        """
        return str(self._com.MessageNumber)

    @property
    def message_as_popup(self) -> bool:
        """Whether the message was raised as a popup rather than in the status bar.

        Per the SAP GUI Scripting API documentation. Only ``False`` observed live.
        """
        return bool(self._com.MessageAsPopup)

    @property
    def message_has_long_text(self) -> bool:
        """Per the SAP GUI Scripting API documentation, whether the message has a long text.

        Observed live: ``True`` for DS 017, ``False`` for S# 343.
        """
        return bool(self._com.MessageHasLongText)

    def message_parameter(self, index: int) -> str:
        """Return the message parameter (``&1`` .. ``&4`` placeholder value) at ``index``.

        Index 0 observed as the ``&1`` value. The T100 message variables are ``&1``–``&4``;
        indices up to 9 returned ``""`` without raising.
        """
        return str(self._com.MessageParameter(index))


class GuiStatusPane(GuiVComponent):
    """Individual pane within the status bar (TypeAsNumber 43)."""


class GuiVHViewSwitch(GuiVComponent):
    """View switch control in the status bar area (TypeAsNumber 129)."""
