"""An optional photo on a movement: one per movement, kept by the t6 API, shown and changed here (#40).

The app never stores a photo: an upload goes straight to `movement photo-add`, and the picture is read
from the API for each request. An upload the API cannot take is not queued, because the list of writes
waiting to be sent shows every value and would carry the whole file.
"""

import base64
import binascii
import re
from collections.abc import Callable, Mapping
from typing import Any

from flask import Flask, Response, abort, redirect, render_template, request
from t6.verbs import Done, MovementPhotoRefInput
from werkzeug.datastructures import FileStorage

from forms import new_key, posted_key
from gateway import ApiRefused, ApiUnavailable, Gateway, Queued, describe

LIMIT = 10 * 1024 * 1024  # the API's limit for one photo
TOO_LARGE = "A photo may be at most 10 MB."
WRONG_TYPE = "A photo must be a JPEG, PNG or WebP image."
NO_PHOTO = "Choose a photo first."
# A form with a photo may be the photo plus the other fields; anything larger is refused before it is read.
FORM_BYTES = LIMIT + 65536
ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_PHOTO_POST = re.compile(r"/movements/[A-Za-z0-9_-]{1,80}/(photo|correct)")
PHOTO_TYPES = ("image/png", "image/jpeg", "image/webp")


class PhotoRefused(ValueError):
    """A file this app will not send, with the plain sentence that says why."""


def allow_photo(path: str) -> None:
    """Raise the body limit for the forms that carry a photo; call it before the form is read."""
    if _PHOTO_POST.fullmatch(path):
        request.max_content_length = FORM_BYTES


def is_photo_form(path: str) -> bool:
    return _PHOTO_POST.fullmatch(path) is not None


def media_type(data: bytes) -> str | None:
    """The image type from the file's first bytes, whatever its name or the browser says."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def chosen_photo(files: Mapping[str, FileStorage]) -> tuple[bytes, str] | None:
    """The photo a form carries, if one was chosen; a file this app will not send raises `PhotoRefused`."""
    upload = files.get("photo")
    if upload is None or not upload.filename:
        return None
    data = upload.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise PhotoRefused(TOO_LARGE)
    kind = media_type(data)
    if kind is None:
        raise PhotoRefused(WRONG_TYPE)
    return data, kind


def send_photo(gateway: Gateway, movement: str, photo: tuple[bytes, str], key: str) -> Done | str:
    """Add or replace the photo of `movement`: done, or the reason it was not."""
    data, kind = photo
    values = {
        "movement": movement,
        "photo": base64.b64encode(data).decode(),
        "media_type": kind,
    }
    outcome = gateway.submit("movement_photo_add", values, key)
    assert not isinstance(
        outcome, Queued
    )  # an upload is never queued: the API answers or is unavailable
    return outcome if isinstance(outcome, Done) else describe(outcome)


def register_photos(
    app: Flask,
    gateway: Gateway,
    csrf: Callable[[], str],
    movement_of: Callable[[str], dict[str, Any]],
    display: Callable[[Any], str],
) -> None:
    def page(movement: dict[str, Any], error: str | None = None, status: int = 200):
        return render_template(
            "movement.html",
            m=movement,
            error=error,
            display=display,
            csrf=csrf(),
            new_key=new_key,
        ), status

    @app.errorhandler(413)
    def too_large(error: Exception):
        if is_photo_form(request.path):
            return render_template(
                "notice.html", error=TOO_LARGE, back=request.path.rsplit("/", 1)[0], label="Back"
            ), 413
        return error

    @app.get("/movements/<movement_id>")
    def movement_page(movement_id: str):
        return page(movement_of(movement_id))

    @app.get("/movements/<movement_id>/photo")
    def movement_photo(movement_id: str):
        """The photo, read from the API for this one request: never stored or logged here."""
        if not ID.fullmatch(movement_id):
            abort(404)
        try:
            read = gateway.read("movement_photo_get", MovementPhotoRefInput(movement=movement_id))
            data = base64.b64decode(read["photo"], validate=True)
        except ApiRefused as error:
            if error.rule in ("photo", "movement"):
                abort(404)
            raise
        except (binascii.Error, KeyError, TypeError):
            raise ApiUnavailable from None
        if read.get("media_type") not in PHOTO_TYPES:
            raise ApiUnavailable
        return Response(
            data,
            mimetype=read["media_type"],
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.post("/movements/<movement_id>/photo")
    def add_movement_photo(movement_id: str):
        movement = movement_of(movement_id)
        key = posted_key(request.form)
        try:
            photo = chosen_photo(request.files)
        except PhotoRefused as refused:
            return page(movement, str(refused), 400)
        if photo is None:
            return page(movement, NO_PHOTO, 400)
        sent = send_photo(gateway, movement_id, photo, key)
        if isinstance(sent, str):
            return page(movement, sent, 400)
        return redirect("/movements/" + movement_id, code=303)

    @app.post("/movements/<movement_id>/photo/delete")
    def delete_movement_photo(movement_id: str):
        movement = movement_of(movement_id)
        outcome = gateway.submit(
            "movement_photo_delete", {"movement": movement_id}, posted_key(request.form)
        )
        assert not isinstance(outcome, Queued)
        if not isinstance(outcome, Done):
            return page(movement, describe(outcome), 400)
        return redirect("/movements/" + movement_id, code=303)
