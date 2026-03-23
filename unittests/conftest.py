"""Pytest configuration and shared fixtures for sapsucker tests."""

from __future__ import annotations

import socket
from collections.abc import Generator
from unittest.mock import MagicMock, PropertyMock

import pytest

# =============================================================================
# SAP INTEGRATION TEST MACHINE CHECK
# =============================================================================

_AUTHORIZED_SAP_TEST_MACHINES = {"HF-KKLEIN3", "HF-MeiskeJ"}


def is_sap_integration_test_machine() -> bool:
    """Check if the current machine is authorized to run SAP integration tests.

    SAP integration tests require access to a real SAP GUI system,
    which is only available on specific developer machines.
    """
    return socket.gethostname() in _AUTHORIZED_SAP_TEST_MACHINES


# =============================================================================
# MOCK HELPERS
# =============================================================================


def make_mock_com(
    type_as_number: int = 1,
    type_name: str = "GuiVComponent",
    id: str = "/app/con[0]/ses[0]/wnd[0]/usr/txtFIELD",
    name: str = "txtFIELD",
    container_type: bool = False,
    text: str = "",
    tooltip: str = "",
    changeable: bool = True,
    parent: MagicMock | None = None,
    height: int = 20,
    width: int = 100,
    left: int = 0,
    top: int = 0,
    screen_left: int = 0,
    screen_top: int = 0,
    modified: bool = False,
    icon_name: str = "",
    is_symbol_font: bool = False,
    acc_text: str = "",
    acc_tooltip: str = "",
    acc_text_on_request: str = "",
    default_tooltip: str = "",
    children: list[MagicMock] | None = None,
    **extra_props,
) -> MagicMock:
    """Create a MagicMock simulating a SAP GUI COM dispatch object."""
    mock = MagicMock()
    mock.TypeAsNumber = type_as_number
    mock.Type = type_name
    mock.Id = id
    mock.Name = name
    mock.ContainerType = container_type
    mock.Text = text
    mock.Tooltip = tooltip
    mock.Changeable = changeable
    mock.Parent = parent
    mock.Height = height
    mock.Width = width
    mock.Left = left
    mock.Top = top
    mock.ScreenLeft = screen_left
    mock.ScreenTop = screen_top
    mock.Modified = modified
    mock.IconName = icon_name
    mock.IsSymbolFont = is_symbol_font
    mock.AccText = acc_text
    mock.AccTooltip = acc_tooltip
    mock.AccTextOnRequest = acc_text_on_request
    mock.DefaultTooltip = default_tooltip

    if children is not None:
        mock.Children = MagicMock()
        mock.Children.Count = len(children)
        mock.Children.Item = lambda i: children[i]
        mock.Children.__iter__ = lambda self: iter(children)
        mock.Children.__len__ = lambda self: len(children)
    else:
        mock.Children = None

    for key, value in extra_props.items():
        setattr(mock, key, value)

    return mock


@pytest.fixture
def mock_com():
    """Return a default mock COM object."""
    return make_mock_com()


# =============================================================================
# SAP DESKTOP SESSION FIXTURE
# =============================================================================


@pytest.fixture
def sap_desktop_session() -> Generator:
    """Provide a logged-in SAP GUI desktop session for integration tests.

    Skips if not on the authorized SAP test machine or if required env vars are missing.
    """
    if not is_sap_integration_test_machine():
        pytest.skip("Not on SAP integration test machine")

    from dotenv import load_dotenv

    load_dotenv()

    import os

    from sapsucker.login import login, logoff

    if not os.environ.get("SAP_CONNECTION_NAME"):
        pytest.skip("SAP_CONNECTION_NAME not set")
    if not os.environ.get("SAP_USER"):
        pytest.skip("SAP_USER not set")
    if not os.environ.get("SAP_PASSWORD"):
        pytest.skip("SAP_PASSWORD not set")
    if not os.environ.get("SAP_MANDANT"):
        pytest.skip("SAP_MANDANT not set")

    session = login(
        connection_name=os.environ["SAP_CONNECTION_NAME"],
        client=os.environ["SAP_MANDANT"],
        user=os.environ["SAP_USER"],
        password=os.environ["SAP_PASSWORD"],
        language=os.environ.get("SAP_LANGUAGE", "DE"),
    )
    yield session
    logoff(session)
