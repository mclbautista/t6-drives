"""Release a shipment, list shipments and show a shipment's manifest, all through the t6 API."""

from collections.abc import Callable

from flask import Flask, render_template, request
from t6.verbs import DriveListInput, DriveShipmentInput, DriveShipmentsInput

from forms import finish, new_key, posted_key
from gateway import Gateway


def register_shipments(app: Flask, gateway: Gateway, csrf: Callable[[], str]) -> None:
    def release_form(error: str | None = None):
        drives = gateway.read("drive_list", DriveListInput())["drives"]
        return render_template(
            "shipment_form.html",
            drives=drives,
            chosen=request.form.getlist("drive_id"),
            form=request.form,
            projects=gateway.projects(),
            error=error,
            csrf=csrf(),
            new_key=new_key,
        )

    @app.route("/shipments/new", methods=["GET", "POST"])
    def release_shipment():
        if request.method == "GET":
            return release_form()
        form = request.form
        if not form.get("party_kind"):
            return release_form("Choose the party kind, contact or studio."), 400
        values = {
            "drives": list(dict.fromkeys(form.getlist("drive_id"))),
            "project": form.get("project", "").strip(),
            "party": form.get("party", "").strip(),
            "party_kind": form["party_kind"],
        }
        for name in ("on", "reason", "recipient"):
            if form.get(name, "").strip():
                values[name] = form[name].strip()
        outcome = gateway.submit("drive_release", values, posted_key(form))
        return finish(
            outcome,
            lambda done: "/shipments/" + done.result["shipment_id"],
            release_form,
            missing_is_404=False,
        )

    @app.get("/shipments")
    def shipment_list():
        listed = gateway.read("drive_shipments", DriveShipmentsInput())["shipments"]
        return render_template("shipments.html", shipments=listed, display=gateway.project_display)

    @app.get("/shipments/<key>")
    def shipment_detail(key: str):
        shipment = gateway.read("drive_shipment", DriveShipmentInput(shipment=key))
        return render_template(
            "manifest.html",
            id=shipment["shipment_id"],
            e=shipment["releases"][0]["event"],
            recipient=shipment["recipient"],
            facts_at_release=shipment["facts_at_release"],
            releases=shipment["releases"],
            display=gateway.project_display,
        )
