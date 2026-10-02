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
        """
        return str(self._com.MessageId).rstrip()

    @property
    def message_number(self) -> str:
        """Message number within the message class, as the zero-padded string SAP GUI returns.

        Observed ``"017"`` and ``"343"``; ``""`` on an empty bar.
        """
        return str(self._com.MessageNumber)

    @property
    def message_as_popup(self) -> bool:
        """Whether the message was raised as a popup rather than in the status bar.

        Observed ``False`` for the messages checked live; the ``True`` case was not reproduced.
        """
        return bool(self._com.MessageAsPopup)

    @property
    def message_has_long_text(self) -> bool:
        """Whether the current message has a long text that can be fetched.

        Observed ``False`` for the messages checked live; the ``True`` case was not reproduced.
        """
        return bool(self._com.MessageHasLongText)

    def message_parameter(self, index: int) -> str:
        """Return the message parameter (``&1`` .. ``&4`` placeholder value) at ``index``.

        ``MessageParameter`` is a COM method, not an indexed property. Returns ``""`` for
        unset parameters (observed for indices up to 9; no exception was raised).
        """
        return str(self._com.MessageParameter(index))


class GuiStatusPane(GuiVComponent):
    """Individual pane within the status bar (TypeAsNumber 43)."""


class GuiVHViewSwitch(GuiVComponent):
    """View switch control in the status bar area (TypeAsNumber 129)."""
