"""Custody screen: append receive, return and retire events through the t6 API. History comes with the Drive."""

from collections.abc import Callable

from flask import Flask, abort, render_template, request
from t6.verbs import DriveGetInput

from forms import finish, new_key, notice, posted_key
from gateway import Gateway

VERBS = {"received": "drive_receive", "returned": "drive_return", "retired": "drive_retire"}


def register_custody(app: Flask, gateway: Gateway, csrf: Callable[[], str]) -> None:
    @app.route("/drive/<drive_id>/custody", methods=["GET", "POST"])
    @app.route("/drive/<drive_id>/retire", methods=["GET", "POST"])
    def record_custody(drive_id: str):
        if request.method == "GET":
            gateway.read("drive_get", DriveGetInput(drive=drive_id))
            return render_template(
                "custody_form.html",
                drive_id=drive_id,
                kinds={"retired": "drive_retire"} if request.path.endswith("/retire") else VERBS,
                projects=gateway.projects(),
                csrf=csrf(),
                new_key=new_key,
            )
        form = request.form
        method = VERBS.get(form.get("kind", ""))
        if method is None or (request.path.endswith("/retire") and method != "drive_retire"):
            abort(400)
        values = {"drive": drive_id, "project": form.get("project", "").strip()}
        if method == "drive_retire":
            values["reason"] = form.get(
                "reason", ""
            ).strip()  # a missing reason is for the API to refuse
        for name in ("on",) if method == "drive_retire" else ("on", "party", "party_kind"):
            if form.get(name, "").strip():
                values[name] = form[name].strip()
        outcome = gateway.submit(method, values, posted_key(form))
        return finish(
            outcome,
            lambda done: "/drive/" + drive_id,
            lambda error: notice(error, "/drive/" + drive_id),
        )
