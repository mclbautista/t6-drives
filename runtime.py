"""Container WSGI factory; runtime secrets never enter image build arguments."""

import os
from pathlib import Path

from flask import Flask

from app import create_app


def application() -> Flask:
    secret = os.environ.get("DRIVES_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Set a runtime session secret of at least 32 characters.")
    database = os.environ.get("DRIVES_DB", "/data/drives.sqlite")
    if not Path(database).is_absolute() or Path(database).parent != Path("/data"):
        raise RuntimeError("Container database must be directly inside the dedicated /data volume.")
    app = create_app(
        database,
        custody_actor_policy=os.environ.get("DRIVES_ACTOR_POLICY", "self-declared-required"),
    )
    app.secret_key = secret
    return app
