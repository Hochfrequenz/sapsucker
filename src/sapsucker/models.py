"""Pydantic data models for SAP GUI element information."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["ElementInfo", "SessionInfo"]


class SessionInfo(BaseModel):
    """Structured information about a SAP GUI session."""

    system_name: str
    client: str
    user: str
    language: str
    transaction: str
    program: str
    screen_number: int
    application_server: str
    response_time: int
    round_trips: int


class ElementInfo(BaseModel):
    """Structured information about a SAP GUI element and its children."""

    id: str
    type: str
    type_as_number: int
    name: str
    text: str
    changeable: bool
    children: list[ElementInfo] = []

    # Extended properties (all have safe defaults for backward compatibility)
    tooltip: str = ""
    default_tooltip: str = ""
    icon_name: str = ""
    modified: bool = False
    acc_text: str = ""
    acc_tooltip: str = ""
    acc_text_on_request: str = ""
    height: int = 0
    width: int = 0
    left: int = 0
    top: int = 0
    screen_left: int = 0
    screen_top: int = 0
    is_symbol_font: bool = False
    container_type: bool = False
