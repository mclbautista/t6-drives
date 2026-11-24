"""Reload now: the sheets replace the Drive data, after a summary and a confirm. Only in practice mode."""

from collections.abc import Callable

from flask import Flask, redirect, render_template, request, session
from t6.verbs import Done, DriveReloadReportInput, Outcome, Preview, Refused

from forms import new_key, notice, posted_key
from gateway import Gateway, describe

BACK = ("/exceptions", "Exceptions")


def register_reload(app: Flask, gateway: Gateway, csrf: Callable[[], str]) -> None:
    def refusal(outcome: Outcome):
        """Why nothing was reloaded, in plain words."""
        status = 400
        if isinstance(outcome, Refused) and outcome.rule == "cutover":
            status = 409
            text = (
                "The system is live, so the sheets can no longer replace the records. "
                "Nothing was changed."
            )
        elif isinstance(outcome, Refused) and outcome.rule == "sheet_changed":
            status = 409
            text = (
                "The sheet changed since you looked. Nothing was changed; press Reload now again."
            )
        elif isinstance(outcome, Refused) and outcome.rule == "token_used":
            status = 409
            text = "This reload was already done. Nothing more was changed."
        elif isinstance(outcome, Refused) and outcome.rule == "config":
            text = "Reloading from the sheets is not set up on the server. Nothing was changed."
        elif isinstance(outcome, Refused) and outcome.rule == "sheet":
            text = "The sheets could not be read. Nothing was changed. " + outcome.reason
        elif isinstance(outcome, Preview):
            text = "Look at the summary first. Nothing was changed."
        else:
            text = describe(outcome) + " Nothing was changed."
        return notice(text, *BACK), status

    @app.get("/exceptions/reload")
    def reload_summary():
        key = new_key()
        outcome = gateway.reload(key)
        if not isinstance(outcome, Preview):
            return refusal(outcome)
        report = gateway.read("drive_reload_report", DriveReloadReportInput())["report"]
        return render_template(
            "reload.html", report=report, token=outcome.token, key=key, csrf=csrf()
        )

    @app.post("/exceptions/reload")
    def reload_now():
        key = posted_key(request.form)
        outcome = gateway.reload(key, request.form.get("token", "") or None)
        if not isinstance(outcome, Done):
            return refusal(outcome)
        result = outcome.result
        rows, drives = result["rows_read"], result["drives"]
        session["flash"] = (
            f"Reloaded: {rows} row{'' if rows == 1 else 's'} read, "
            f"{drives} Drive{'' if drives == 1 else 's'}, "
            f"{result['exceptions']} not loaded."
        )
        return redirect("/exceptions", code=303)
