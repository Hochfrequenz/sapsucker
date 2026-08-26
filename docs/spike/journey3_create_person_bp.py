"""SPIKE ARTEFACT — not part of the sapsucker package. See #82.

Produced by an AI from a recorded SAP GUI journey plus two sentences of
human context, with no other input. Kept verbatim (apart from a masked phone
number) so the reconstruction can be judged as it was generated. It has known
weaknesses, documented in #82; do not treat it as reference code.

It is excluded from linting and formatting and is not kept compiling against the
package; expect it to drift out of date with sapsucker's internals.

Create a Business Partner of type *Person* in transaction BP.

Reconstructed from a SAP GUI script recording (journey3_bp.vbs) plus the
recorder's own note:

    "i opened transaction bp, chose person as bp type, filled the form and
     clicked on sichern. a popup appeared informing me about errors with the
     phone number. i clicked ok. then it was saved."

Prerequisites:
    - SAP GUI for Windows running, logged in, scripting enabled.
    - Authorization for BP (create person) in the target client.

WARNING: this script WRITES to SAP. Run it against a sandbox client first.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, cast

from sapsucker import SapGui
from sapsucker._errors import SapGuiError, SapGuiTimeoutError
from sapsucker.components.session import GuiSession

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Fixed element IDs.
#
# These are NOT parameters. They encode the BDT screen layout of BP for a
# business partner of type *Person* on the "Address" tab (TAB_01). Every ID
# below is copied verbatim from the recording. If SAP re-generates the BDT
# screen sequence (different subscreen numbers) these break — see the
# "production-safe" notes: the right fix is discovery, not parameters.
# --------------------------------------------------------------------------

_TAB_01 = (
    "wnd[0]/usr"
    "/subSCREEN_3000_RESIZING_AREA:SAPLBUS_LOCATOR:2000"
    "/subSCREEN_1010_RIGHT_AREA:SAPLBUPA_DIALOG_JOEL:1000"
    "/ssubSCREEN_1000_WORKAREA_AREA:SAPLBUPA_DIALOG_JOEL:1100"
    "/ssubSCREEN_1100_MAIN_AREA:SAPLBUPA_DIALOG_JOEL:1101"
    "/tabsGS_SCREEN_1100_TABSTRIP/tabpSCREEN_1100_TAB_01"
    "/ssubSCREEN_1100_TABSTRIP_AREA:SAPLBUSS:0028"
    "/ssubGENSUB:SAPLBUSS:7016"
)
_ADDRESS = f"{_TAB_01}/subA05P01:SAPLBUA0:0400/subADDRESS:SAPLSZA7:0600/subCOUNTRY_SCREEN:SAPLSZA7:0601"

ID_TITLE = f"{_TAB_01}/subA02P01:SAPLBUD0:1130/cmbBUS000FLDS-TITLE_MEDI"
ID_FIRST_NAME = f"{_TAB_01}/subA02P03:SAPLBUD0:1301/txtBUT000-NAME_FIRST"
ID_LAST_NAME = f"{_TAB_01}/subA02P04:SAPLBUD0:1302/txtBUT000-NAME_LAST"
ID_STREET = f"{_ADDRESS}/txtADDR2_DATA-STREET"
ID_HOUSE_NUMBER = f"{_ADDRESS}/txtADDR2_DATA-HOUSE_NUM1"
ID_POSTAL_CODE = f"{_ADDRESS}/txtADDR2_DATA-POST_CODE1"
ID_CITY = f"{_ADDRESS}/txtADDR2_DATA-CITY1"
ID_COUNTRY = f"{_ADDRESS}/ctxtADDR2_DATA-COUNTRY"
ID_PHONE = f"{_TAB_01}/subA06P01:SAPLBUA0:0700/subADDR_ICOMM:SAPLSZA11:0100/txtSZA11_0100-TEL_NUMBER"

ID_MAIN_WINDOW = "wnd[0]"
ID_POPUP = "wnd[1]"
ID_OKCODE = "wnd[0]/tbar[0]/okcd"
ID_STATUSBAR = "wnd[0]/sbar"
ID_SAVE_BUTTON = "wnd[0]/tbar[0]/btn[11]"

# Application-toolbar button that switches BP into "create person" mode.
# The recording pressed tbar[1]/btn[5]; the note says "chose person as bp
# type", so btn[5] == "Person". Index-addressed toolbar buttons are the
# single most fragile thing in this script (see guesses).
ID_CREATE_PERSON_BUTTON = "wnd[0]/tbar[1]/btn[5]"

# Buttons pressed on the post-save popup(s), in recorded order.
POPUP_BUTTON_NAMES = ("btnBUTTON_1", "btnBUTTON_2")

TRANSACTION_CODE = "/nbp"
VKEY_ENTER = 0


@dataclass(frozen=True)
class PersonBusinessPartner:
    """The data that legitimately differs from one run to the next."""

    first_name: str
    last_name: str
    street: str
    house_number: str
    postal_code: str
    city: str
    country: str = "DE"
    phone: str | None = None
    # Language-independent key into TSAD3 (address title). "0002" was
    # recorded; on a standard SAP client that is "Mr."/"Herr" and matches
    # the recorded first name "Maximilian".
    title_key: str = "0002"


@dataclass(frozen=True)
class CreateResult:
    """What the automation observed after pressing Save."""

    status_message: str
    status_message_type: str
    business_partner_id: str | None
    popup_messages: tuple[str, ...]
    field_mismatches: tuple[tuple[str, str, str], ...]

    @property
    def saved(self) -> bool:
        """True when the status bar reported success and a BP number was parsed."""
        return self.status_message_type in ("S", "") and self.business_partner_id is not None


class BusinessPartnerNotSavedError(SapGuiError):
    """Raised when BP did not confirm a successful save."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
# find_by_id is typed as returning `GuiComponent | None` (base.py:179), so
# every concrete member access needs a narrowing step. The library itself
# uses `cast(Any, ...)` for exactly this (login.py:147-151); these helpers
# keep that cast in one place instead of sprinkling it through the flow.


def _element(session: GuiSession, element_id: str) -> Any:
    """Return the element at *element_id* or raise ElementNotFoundError."""
    return cast(Any, session.find_by_id(element_id))


def _optional_element(session: GuiSession, element_id: str) -> Any | None:
    """Return the element at *element_id*, or None if it is not on screen."""
    found = session.find_by_id(element_id, raise_error=False)
    return None if found is None else cast(Any, found)


def _wait_until_idle(session: GuiSession, timeout: float = 30.0) -> None:
    """Block until the session has finished its server round trip.

    Nothing in the recording waits: the recorder is replayed by SAP GUI
    itself, which serialises calls. A Python client driving COM from
    outside can issue the next call while the server is still busy, so
    every screen change here is followed by this.
    """
    deadline = time.monotonic() + timeout
    while session.busy:
        if time.monotonic() > deadline:
            raise SapGuiTimeoutError(f"session still busy after {timeout}s")
        time.sleep(0.1)


def _set_text(session: GuiSession, element_id: str, value: str) -> None:
    _element(session, element_id).text = value


def _read_text(session: GuiSession, element_id: str) -> str:
    return str(_element(session, element_id).text)


# --------------------------------------------------------------------------
# the task
# --------------------------------------------------------------------------


def create_person_business_partner(
    session: GuiSession,
    person: PersonBusinessPartner,
    *,
    accept_warning_popups: bool = True,
    max_popups: int = 4,
) -> CreateResult:
    """Create a Person business partner in BP and report what SAP said.

    Args:
        session: A logged-in SAP GUI session.
        person: The business partner master data to enter.
        accept_warning_popups: Press the recorded confirmation buttons on the
            post-save popup(s). The recording only ever confirms; set False to
            make an unexpected popup an error instead of a blind click.
        max_popups: Safety bound on the popup-dismissal loop.

    Returns:
        A CreateResult carrying the status-bar outcome, the parsed BP number,
        the text of every popup that appeared, and any field whose readback
        did not match what was typed.

    Raises:
        BusinessPartnerNotSavedError: SAP reported an error, or no popup
            handler was allowed and a popup blocked the save, or the save
            could not be confirmed.
        ElementNotFoundError: The BP create screen did not look as expected.
    """
    # 1. Open BP. The okcd text and the Enter are one indivisible action:
    #    setting .text alone does nothing (okcode.py:13-15).
    _element(session, ID_OKCODE).text = TRANSACTION_CODE
    _element(session, ID_MAIN_WINDOW).send_v_key(VKEY_ENTER)
    _wait_until_idle(session)

    # 2. Switch to "create person". Without this the BDT field IDs below do
    #    not exist at all, so this press is load-bearing, not navigation.
    _element(session, ID_CREATE_PERSON_BUTTON).press()
    _wait_until_idle(session)

    # Fail fast and loudly if we are not on the screen the recording saw,
    # rather than half-filling a form. The recording has no equivalent.
    if _optional_element(session, ID_FIRST_NAME) is None:
        raise BusinessPartnerNotSavedError(
            "BP create-person screen not recognised: "
            f"{ID_FIRST_NAME!r} is not on screen "
            f"(transaction={session.info.transaction!r}, screen={session.info.screen_number})"
        )

    # 3. Title. sapsucker's GuiComboBox wraps only COM `Value` (the
    #    language-dependent display text, combobox.py:45-51) and has no
    #    `Key` wrapper, but the recording sets `.key` — the
    #    language-independent code. Fall back to the raw COM object
    #    (`.com`, base.py:31) so this works in an EN or DE session alike.
    title_combo = _element(session, ID_TITLE)
    title_combo.com.Key = person.title_key

    # 4. Name, address, phone.
    typed: list[tuple[str, str, str]] = [
        ("first_name", ID_FIRST_NAME, person.first_name),
        ("last_name", ID_LAST_NAME, person.last_name),
        ("street", ID_STREET, person.street),
        ("house_number", ID_HOUSE_NUMBER, person.house_number),
        ("postal_code", ID_POSTAL_CODE, person.postal_code),
        ("city", ID_CITY, person.city),
        ("country", ID_COUNTRY, person.country),
    ]
    if person.phone is not None:
        typed.append(("phone", ID_PHONE, person.phone))

    for _label, element_id, value in typed:
        _set_text(session, element_id, value)

    # Read every field back before saving. SAP silently truncates input at
    # MaxLength and BDT conversion exits can rewrite a value in place; the
    # recorded run produced a phone-number complaint, which is exactly the
    # class of problem a readback surfaces at the right moment.
    mismatches: list[tuple[str, str, str]] = []
    for label, element_id, value in typed:
        actual = _read_text(session, element_id)
        if actual.strip() != value.strip():
            mismatches.append((label, value, actual))
            logger.warning(
                "bp_field_readback_mismatch",
                extra={"field": label, "typed": value, "on_screen": actual},
            )

    # 5. Save (Ctrl+S / the toolbar disk).
    _element(session, ID_SAVE_BUTTON).press()
    _wait_until_idle(session)

    # 6. Confirm the popup(s). The recording presses btnBUTTON_1 then
    #    btnBUTTON_2, both under wnd[1] — it is ambiguous whether that is
    #    two buttons in one dialog or one button in each of two dialogs
    #    (the note mentions only one). This loop handles both: try the
    #    recorded buttons in the recorded order, re-checking after each
    #    press whether the popup is still up.
    popup_messages: list[str] = []
    for _ in range(max_popups):
        popup = _optional_element(session, ID_POPUP)
        if popup is None:
            break
        message = str(popup.text)
        popup_messages.append(message)
        if not accept_warning_popups:
            raise BusinessPartnerNotSavedError(f"popup blocked the save and was not confirmed: {message!r}")
        logger.info("bp_save_popup", extra={"popup_text": message})

        pressed = False
        for button_name in POPUP_BUTTON_NAMES:
            button = _optional_element(session, f"{ID_POPUP}/usr/{button_name}")
            if button is None:
                continue
            button.press()
            pressed = True
            _wait_until_idle(session)
            if _optional_element(session, ID_POPUP) is None:
                break
        if not pressed:
            # An unrecognised dialog. Do not guess a keystroke into a
            # write transaction — stop and let a human look.
            raise BusinessPartnerNotSavedError(
                f"unexpected popup with none of {POPUP_BUTTON_NAMES}: {message!r}"
            )

    if _optional_element(session, ID_POPUP) is not None:
        raise BusinessPartnerNotSavedError(f"popups did not clear after {max_popups} confirmations")

    # 7. Verify. The recording ends at the last click and never looks at the
    #    result; the note's "then it was saved" is the human reading the
    #    status bar. Read it explicitly.
    _wait_until_idle(session)
    statusbar = _element(session, ID_STATUSBAR)
    status_message = str(statusbar.text)
    status_message_type = str(statusbar.message_type)

    result = CreateResult(
        status_message=status_message,
        status_message_type=status_message_type,
        business_partner_id=_parse_business_partner_id(status_message),
        popup_messages=tuple(popup_messages),
        field_mismatches=tuple(mismatches),
    )

    if status_message_type in ("E", "A", "W"):
        raise BusinessPartnerNotSavedError(f"BP save reported {status_message_type}: {status_message!r}")
    if result.business_partner_id is None:
        raise BusinessPartnerNotSavedError(
            f"BP save could not be confirmed; status bar said {status_message!r} "
            f"(type={status_message_type!r})"
        )
    logger.info("bp_created", extra={"business_partner_id": result.business_partner_id})
    return result


_BP_ID_PATTERN = re.compile(r"\b(\d{4,10})\b")


def _parse_business_partner_id(status_message: str) -> str | None:
    """Pull the BP number out of a status message like 'Business partner 1000123 created'."""
    match = _BP_ID_PATTERN.search(status_message)
    return match.group(1) if match else None


def main() -> None:
    """Recreate the recorded run against the first session of the first connection."""
    logging.basicConfig(level=logging.INFO)
    app = SapGui.connect()
    session = cast(GuiSession, app.connections[0].sessions[0])  # type: ignore[attr-defined]

    person = PersonBusinessPartner(
        first_name="Maximilian",
        last_name="Mustermann",
        street="Teststr",
        house_number="17",
        postal_code="11155",
        city="Berlin",
        country="DE",
        phone="+491700000000",   # masked; the recorded value provoked the warning
        title_key="0002",
    )
    result = create_person_business_partner(session, person)
    print(f"created BP {result.business_partner_id}: {result.status_message}")
    for popup_text in result.popup_messages:
        print(f"  popup: {popup_text}")
    for field, typed_value, on_screen in result.field_mismatches:
        print(f"  field {field}: typed {typed_value!r}, screen showed {on_screen!r}")


if __name__ == "__main__":
    main()
