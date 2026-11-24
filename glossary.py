"""The plain words the pages use, in one place, instead of the system's words.

The wording test (tests/test_plain_pages.py) fails when a page shows one of the words in `SYSTEM`.
"""

import re
from collections.abc import Mapping
from typing import Any

PROJECT_USE = "Project use"  # not Assignment
MOVEMENT = "movement"  # not custody event
SERIAL = "serial"  # not accepted serial
SERIAL_TO_CHECK = "serial to check"  # not suspected serial
CONFIRM_SERIAL = "Confirm serial"  # not Promote
WAITING_TO_SEND = "Waiting to send"  # not Outbox
RETIRE = "Retire"  # stays
RECYCLE = "Recycle"  # a Drive reused for a new show: new label, new Project
DRIVE = "Drive"  # stays
MOVEMENT_TITLE = "Movement"  # stays

PLAIN = {
    "project_use": PROJECT_USE,
    "movement": MOVEMENT,
    "serial": SERIAL,
    "serial_to_check": SERIAL_TO_CHECK,
    "confirm_serial": CONFIRM_SERIAL,
    "waiting_to_send": WAITING_TO_SEND,
    "retire": RETIRE,
    "drive": DRIVE,
    "movement_title": MOVEMENT_TITLE,
}

# System words that must not appear on a page (compared in lower case).
SYSTEM = (
    "assignment",
    "custody event",
    "accepted serial",
    "suspected serial",
    "promote",
    "outbox",
    "[person",
)

# What the verbs are called to the person, for the page that lists writes waiting to be sent.
VERB_WORDS = {
    "drive_add": "New Drive",
    "drive_edit": "Change to a Drive",
    "drive_promote_serial": CONFIRM_SERIAL,
    "drive_assign": "Start " + PROJECT_USE,
    "drive_unassign": "End " + PROJECT_USE,
    "drive_receive": "Drive received",
    "drive_return": "Drive returned",
    "drive_recycle": RECYCLE + " Drive",
    "drive_retire": RETIRE + " Drive",
    "drive_release": "Shipment released",
    "drive_catalog_snapshot": "Scan of a Drive",
    "movement_correct": "Correction of a " + MOVEMENT,
    "drive_capture": "Photo given a Drive",
    "movement_photo_add": "Photo of a " + MOVEMENT,
    "movement_photo_delete": "Photo of a " + MOVEMENT + " deleted",
}

# An internal ID: it may sit in a link or a form value, never in what a person reads.
ID_PATTERN = re.compile(r"t6[a-z]_[0-9a-f]+")
DRIVE_ID = re.compile(r"(?:Drive )?t6d_[0-9a-f]+")
PROJECT_ID = re.compile(r"(?:Project )?t6p_[0-9a-f]+")


def looks_like_id(text: str) -> bool:
    return ID_PATTERN.search(text) is not None


# A message from the API may still use a system word; this is what it is called to the person.
SAY = (
    (re.compile(r"suspected serial", re.IGNORECASE), SERIAL_TO_CHECK),
    (re.compile(r"accepted serial", re.IGNORECASE), SERIAL),
    (re.compile(r"custody events?", re.IGNORECASE), MOVEMENT),
    (re.compile(r"assignments?", re.IGNORECASE), PROJECT_USE),
    (re.compile(r"promot(e|ed|ing|ion)", re.IGNORECASE), "confirm"),
    (re.compile(r"outbox", re.IGNORECASE), WAITING_TO_SEND.lower()),
)


def plain(text: str) -> str:
    """A message from the API with any ID and system word replaced, for the page to show."""
    text = DRIVE_ID.sub("a Drive", text)
    text = PROJECT_ID.sub("that Project", text)
    text = ID_PATTERN.sub("that record", text)
    for pattern, word in SAY:
        text = pattern.sub(word, text)
    return text


def waiting_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A write waiting to be sent, in plain words: what it is, and the values that are not IDs."""
    shown = [
        (name.replace("_", " "), value)
        for name, value in entry["inputs"].items()
        if isinstance(value, (str, int, float)) and not looks_like_id(str(value))
    ]
    return {**entry, "what": VERB_WORDS.get(entry["verb"], "A change"), "shown": shown}
