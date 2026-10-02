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
        """Message type character (S, W, E, A, I, or empty)."""
        return str(self._com.MessageType)

    # The four members below were found in the type library (issue #90) but have
    # not been read against a live SAP GUI yet; see scripts/probe_statusbar_message.py.
    # In particular: the return type of MessageNumber, and what each returns when
    # the bar holds no message or a non-T100 message, are unverified.

    @property
    def message_id(self) -> str:
        """Message class (T100 ``ARBGB``) of the current message, e.g. ``"00"``."""
        return str(self._com.MessageId)

    @property
    def message_number(self) -> str:
        """Message number within the message class of the current message."""
        return str(self._com.MessageNumber)

    @property
    def message_as_popup(self) -> bool:
        """Whether the message was raised as a popup rather than in the status bar."""
        return bool(self._com.MessageAsPopup)

    @property
    def message_has_long_text(self) -> bool:
        """Whether the current message has a long text that can be fetched."""
        return bool(self._com.MessageHasLongText)


class GuiStatusPane(GuiVComponent):
    """Individual pane within the status bar (TypeAsNumber 43)."""


class GuiVHViewSwitch(GuiVComponent):
    """View switch control in the status bar area (TypeAsNumber 129)."""
