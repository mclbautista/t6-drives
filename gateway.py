"""The one place this app talks to the t6 API: typed reads, and writes that wait in the outbox when the API is down."""

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from t6.client import APIError, Client, Unavailable
from t6.verbs import (
    Ambiguous,
    Done,
    DriveAddInput,
    DriveAssignInput,
    DriveEditInput,
    DrivePromoteSerialInput,
    DriveReceiveInput,
    DriveReleaseInput,
    DriveRetireInput,
    DriveReturnInput,
    DriveUnassignInput,
    NeedsPerson,
    Outcome,
    Preview,
    Refused,
)

from outbox import Outbox

log = logging.getLogger("drives.gateway")
PROJECT_SECONDS = 30.0
WRITES: dict[str, type[BaseModel]] = {
    "drive_add": DriveAddInput,
    "drive_edit": DriveEditInput,
    "drive_promote_serial": DrivePromoteSerialInput,
    "drive_assign": DriveAssignInput,
    "drive_unassign": DriveUnassignInput,
    "drive_receive": DriveReceiveInput,
    "drive_return": DriveReturnInput,
    "drive_retire": DriveRetireInput,
    "drive_release": DriveReleaseInput,
}


@dataclass(frozen=True)
class Queued:
    """A write the API could not take: it waits in the outbox and is retried with its key."""


class ApiUnavailable(Exception):
    """The API cannot be reached, or answered something that is not an outcome."""


class ApiRefused(Exception):
    """A read the API answered with something other than its result."""

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(reason)
        self.rule, self.reason = rule, reason


def call(client: Client, method: str, inputs: BaseModel) -> Outcome | Unavailable:
    """One typed call. A 5xx answer counts as unreachable; the answer's body is never kept or shown."""
    try:
        return getattr(client, method)(inputs)
    except APIError as error:
        found = re.search(r"HTTP (\d{3})", str(error))
        status = int(found.group(1)) if found else 0
        log.warning("t6 API answered HTTP %s", status)
        if status >= 500:
            return Unavailable(f"HTTP {status}")
        return Refused(rule="api", reason="The t6 API did not accept the request.")
    except ValueError:
        log.warning("t6 API answered something that is not an outcome")
        return Unavailable("unreadable answer")


def describe(outcome: Outcome) -> str:
    """What the custodian reads when a write is not done."""
    if isinstance(outcome, (Refused, NeedsPerson)):
        return outcome.reason
    if isinstance(outcome, Ambiguous):
        return "More than one Assignment matches; end it from the Drive page."
    return "The t6 API gave an answer this app cannot use."


class Gateway:
    def __init__(self, client: Client, outbox: Outbox, retry_interval: float) -> None:
        self.client, self.outbox, self.retry_interval = client, outbox, retry_interval
        self._tried = time.monotonic()
        self._projects: tuple[float, list[dict[str, Any]]] | None = None

    def read(self, method: str, inputs: BaseModel) -> dict[str, Any]:
        outcome = call(self.client, method, inputs)
        if isinstance(outcome, Unavailable) or (
            isinstance(outcome, Refused) and outcome.rule == "api"
        ):
            raise ApiUnavailable
        if not isinstance(outcome, Done):
            raise ApiRefused(
                getattr(outcome, "rule", "unexpected"),
                getattr(outcome, "reason", "Unexpected answer."),
            )
        return dict(outcome.result)

    def projects(self) -> list[dict[str, Any]]:
        """Registered Projects only, for a short while: a Drive is tied to a T6 Project ID."""
        from t6.verbs import ProjectListInput

        if self._projects is None or time.monotonic() - self._projects[0] > PROJECT_SECONDS:
            listed = self.read("project_list", ProjectListInput())["projects"]
            self._projects = (time.monotonic(), [p for p in listed if p.get("t6_id")])
        return self._projects[1]

    def project_display(self, ref: Mapping[str, Any] | None) -> str:
        """A Project reference as the screens show it: its name from the API, or the text an import left."""
        if ref is None or (ref["page_id"] is None and ref["text"] is None):
            return "Unknown Project"
        if ref["text"] is not None:
            return "Unresolved: " + ref["text"]
        found = next((p for p in self.projects() if p["t6_id"] == ref["page_id"]), None)
        if found is None:
            return "Project " + ref["page_id"]
        return f"{found['name']} (code: {found['project_code'] or 'unknown'}; T6 Project ID: {found['t6_id']})"

    def _send(self, method: str, values: dict[str, Any], key: str) -> Outcome | Unavailable:
        """The verb's preview, then its confirm, both under `key`: sending it again is the same write."""
        model = WRITES[method]
        outcome = call(self.client, method, model(**values, idempotency_key=key))
        if isinstance(outcome, Preview):
            outcome = call(
                self.client, method, model(**values, idempotency_key=key, confirm=outcome.token)
            )
        return outcome

    def submit(self, method: str, values: dict[str, Any], key: str) -> Outcome | Queued:
        """A write. When the API cannot be reached it is queued, and sent again later with the same key."""
        try:
            WRITES[method](**values, idempotency_key=key)
        except ValidationError:
            return Refused(rule="input", reason="Check the values entered.")
        outcome = self._send(method, values, key)
        if isinstance(outcome, Unavailable):
            self.outbox.add(method, values, key)
            self._tried = time.monotonic()
            return Queued()
        return outcome

    def retry(self) -> None:
        """Send the queued writes again, oldest first, at most once in `retry_interval`; stop at the first one the API cannot take."""
        if time.monotonic() - self._tried < self.retry_interval or not self.outbox.lock.acquire(
            blocking=False
        ):
            return
        try:
            self._tried = time.monotonic()
            for entry in self.outbox.entries():
                if entry["state"] != "queued":
                    continue
                outcome = self._send(entry["verb"], entry["inputs"], entry["key"])
                if isinstance(outcome, Unavailable):
                    return
                if isinstance(outcome, Done):
                    self.outbox.remove(entry["key"])
                else:
                    self.outbox.fail(entry["key"], describe(outcome))
        finally:
            self.outbox.lock.release()
