"""What every form screen shares: the idempotency key a form carries, and how a write's outcome is answered."""

import re
import secrets
from collections.abc import Callable, Mapping

from flask import abort, redirect, render_template
from t6.verbs import Done, Outcome, Refused

from gateway import Queued, describe


def new_key() -> str:
    """Made when a form is rendered: submitting that form twice is the same write."""
    return secrets.token_hex(16)


def posted_key(form: Mapping[str, str]) -> str:
    key = form.get("key", "")
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        abort(400)
    return key


def finish(
    outcome: Outcome | Queued,
    location: Callable[[Done], str],
    again: Callable[[str], str],
    *,
    missing_is_404: bool = True,
):
    """Done goes on to `location`; anything else is shown on the form again, with its reason.

    A step the Drive's state does not allow (`custody`, `drive_changed`) answers 409, any other refusal 400.
    """
    if isinstance(outcome, Done):
        return redirect(location(outcome), code=303)
    if isinstance(outcome, Queued):
        return render_template("queued.html"), 202
    if missing_is_404 and isinstance(outcome, Refused) and outcome.rule == "drive":
        abort(404)
    refused_by_state = isinstance(outcome, Refused) and outcome.rule in ("custody", "drive_changed")
    return again(describe(outcome)), 409 if refused_by_state else 400


def notice(error: str, back: str, label: str = "Drive details") -> str:
    """A refusal for a form that has no page of its own to come back to."""
    return render_template("notice.html", error=error, back=back, label=label)
