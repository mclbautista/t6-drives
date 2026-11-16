"""Container WSGI factory; runtime secrets never enter image build arguments."""

import os
from pathlib import Path

from flask import Flask
from t6.client import Client

from app import create_app


def application() -> Flask:
    secret = os.environ.get("DRIVES_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Set a runtime session secret of at least 32 characters.")
    url, api_secret = os.environ.get("T6_API_URL", ""), os.environ.get("T6_API_SECRET", "")
    if not url or not api_secret:
        raise RuntimeError("Set T6_API_URL and T6_API_SECRET at runtime.")
    outbox = os.environ.get("DRIVES_OUTBOX", "/data/outbox.json")
    if not Path(outbox).is_absolute() or Path(outbox).parent != Path("/data"):
        raise RuntimeError("Container outbox must be directly inside the dedicated /data volume.")
    allowed_host = os.environ.get("DRIVES_ALLOWED_HOST", "")
    app = create_app(Client(url, api_secret), outbox, allowed_host=allowed_host)
    app.secret_key = secret
    return app
