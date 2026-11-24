"""The real t6 API on a temporary database, served over HTTP for the app under test.

The API code is t6's own `VerbAPI`; nothing is faked. A custodian Person holds the one API secret the app
uses. Drives, custody events and Assignments are seeded only by calling verbs through `t6.client.Client`;
Projects are registered in the t6 registry with a Notion page from t6's own Notion fake.
"""

import os
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from typing import Any
from unittest.mock import patch

from flask import Flask
from flask.testing import FlaskClient
from t6.client import Client
from t6.notion.testing import FakeNotion, title
from t6.person import People
from t6.preview import PreviewTokens
from t6.privacy import Person, Registry
from t6.registry import Registered
from t6.registry.sqlite import SqliteProjectRegistry
from t6.server.app import Request, Response
from t6.server.http import CATALOG_BODY_BYTES, CATALOG_SNAPSHOT_PATH, make_server
from t6.server.queue import JobQueue
from t6.server.verbs import VerbAPI
from t6.verbs import Done, Preview

from app import create_app

SECRET = "synthetic-custodian-secret"


class SyntheticAPI:
    """`mode` is "up", "down" (answers 503), or "lost" (does a confirmed write, then answers 503)."""

    def __init__(self, directory: str) -> None:
        self.mode = "up"
        self.requests: list[str] = []
        self.notion = FakeNotion()
        self.notion.add_snapshot_data_source("projects", "projects")
        self.registry = SqliteProjectRegistry(directory + "/registry")
        people = People(self.registry, PreviewTokens("synthetic-signing-key"))

        def person(page: str, actor: str, kind: str = "staff") -> str:
            return str(
                self.people_write(people, "add", {"page_id": page * 32, "kind": kind}, actor)[
                    "person_id"
                ]
            )

        # No public call makes the first operator: a Telegram ID and the operator seed do.
        boss = person("b", "setup")
        self.people_write(people, "set-telegram", {"person_id": boss, "telegram_id": 7}, "setup")
        people.seed_operators([7])
        custodian = self.custodian = person("c", "setup")
        self.people_write(people, "grant", {"person_id": custodian, "permission": "drives"}, boss)
        self.api = VerbAPI(
            people,
            JobQueue(directory + "/queue"),
            {SECRET: custodian, "synthetic-operator-secret": boss},
            lambda _: self.notion.client(),
            privacy_registry=Registry(people=[Person(name="Invented Client")]),
            data_sources={"projects": "projects"},
        )
        self.server: ThreadingHTTPServer | None = None
        self.port = 0
        self.start()

    @staticmethod
    def people_write(
        people: People, verb: str, values: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        key = "setup-" + verb + str(sorted(values.items()))
        preview = people.write(verb, values, actor=actor, idempotency_key=key)
        assert isinstance(preview, Preview), preview
        done = people.write(verb, values, actor=actor, idempotency_key=key, confirm=preview.token)
        assert isinstance(done, Done), done
        return done.result

    def handle(self, request: Request) -> Response:
        self.requests.append(request.path)
        if self.mode == "down":
            return Response(503, {"error": "unavailable"})
        response = self.api(request)
        # The write is confirmed and done, and then the answer to the confirm is lost on the way back.
        lost = self.mode == "lost" and b'"confirm"' in request.body
        return Response(503, {"error": "lost"}) if lost else response

    def start(self) -> None:
        self.server = server = make_server(
            ("127.0.0.1", self.port),
            self.handle,
            body_limits={
                CATALOG_SNAPSHOT_PATH: CATALOG_BODY_BYTES
            },  # as the t6 tailnet listener does
        )
        self.port = server.server_port
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        assert self.server is not None
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self) -> Client:
        return Client(self.url, SECRET, timeout=5)

    # Seeding and reading, only through verbs.

    def project(self, name: str) -> str:
        page = self.notion.add_page("projects", {"Name": title(name)})
        key = "project-" + name
        registered = self.registry.register(
            actor="setup", idempotency_key=key, notion_page_id=page["id"]
        )
        assert isinstance(registered, Registered)
        return registered.project.id

    def write(self, method: str, model: Any, **values: Any) -> Done:
        client = self.client()
        key = method + str(sorted(values.items(), key=str))
        preview = getattr(client, method)(model(**values, idempotency_key=key))
        assert isinstance(preview, Preview), preview
        done = getattr(client, method)(model(**values, idempotency_key=key, confirm=preview.token))
        assert isinstance(done, Done), done
        return done

    def read(self, method: str, model: Any, **values: Any) -> dict[str, Any]:
        done = getattr(self.client(), method)(model(**values))
        assert isinstance(done, Done), done
        return dict(done.result)

    def drives(self) -> list[dict[str, Any]]:
        from t6.verbs import DriveListInput

        return list(self.read("drive_list", DriveListInput)["drives"])

    def drive(self, drive_id: str) -> dict[str, Any]:
        from t6.verbs import DriveGetInput

        return self.read("drive_get", DriveGetInput, drive=drive_id)

    def add_drive(self, **facts: Any) -> str:
        from t6.verbs import DriveAddInput

        return str(self.write("drive_add", DriveAddInput, **facts).result["drive_ids"][0])

    def receive(self, drive: str, project: str, **values: Any) -> None:
        from t6.verbs import DriveReceiveInput

        self.write("drive_receive", DriveReceiveInput, drive=drive, project=project, **values)

    def legacy_assignment(self, drive: str, text: str) -> None:
        """An Assignment that names its Project only by text, as an import left it, through the public store."""
        from datetime import date

        from t6.drives import Assignment, Ref, Role
        from t6.drives.sqlite import SqliteDriveStore

        SqliteDriveStore(self.registry).add_assignment(
            drive,
            Assignment(
                project=Ref(text=text), label="legacy", role=Role.BACKUP, start=date(2024, 1, 1)
            ),
        )

    def import_exception(self, **fields: Any) -> None:
        """A row the Drive import could not read, left in the t6 database by the import, through the public store."""
        from t6.drives.importer import ReviewItem
        from t6.drives.sqlite import SqliteDriveStore

        SqliteDriveStore(self.registry).record_exceptions([ReviewItem(**fields)])

    def assign(self, drive: str, project: str, label: str, role: str, **values: Any) -> None:
        from t6.verbs import DriveAssignInput

        self.write(
            "drive_assign",
            DriveAssignInput,
            drive=drive,
            project=project,
            label=label,
            role=role,
            **values,
        )

    def edit_drive(self, drive: str, **facts: Any) -> None:
        from t6.verbs import DriveEditInput

        self.write("drive_edit", DriveEditInput, drive=drive, **facts)

    def scan(
        self, drive: str, on: str, entries: list[dict[str, Any]], root_path: str = "Volume"
    ) -> None:
        from t6.verbs import DriveCatalogSnapshotInput

        self.write(
            "drive_catalog_snapshot",
            DriveCatalogSnapshotInput,
            drive=drive,
            captured_on=on,
            root_path=root_path,
            entries=entries,
        )

    def legacy_shipment(self, drive: str, project: str, shipment: str) -> None:
        """A release recorded as the workbook import does it: with a shipment, and no facts kept for it."""
        from datetime import date

        from t6.drives import CustodyEvent, CustodyKind, Party, PartyKind, Ref
        from t6.drives.sqlite import SqliteDriveStore

        SqliteDriveStore(self.registry).append_event(
            drive,
            CustodyEvent(
                kind=CustodyKind.RELEASED,
                on=date(2024, 1, 1),
                party=Party(PartyKind.STUDIO, Ref(text="Invented Studio")),
                project=Ref(page_id=project),
                logged_by=Ref(page_id=self.custodian),
                reason=None,
                shipment_id=shipment,
            ),
        )


class DriveAppCase(unittest.TestCase):
    """The app on a temporary outbox, against `self.api`; `self.web` is its Flask test client."""

    app: Flask
    web: FlaskClient

    def setUp(self) -> None:
        env = patch.dict(
            os.environ,
            {"T6_NOTION_TOKEN": "synthetic", "T6_PREVIEW_SIGNING_KEY": "synthetic-signing-key"},
        )
        env.start()
        self.addCleanup(env.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.api = SyntheticAPI(self.directory)
        self.addCleanup(self.stop_api)
        self.outbox = self.directory + "/outbox.json"
        self.app = create_app(self.api.client(), self.outbox, retry_interval=0)
        self.web = self.app.test_client()

    def stop_api(self) -> None:
        if self.api.server is not None:
            self.api.stop()

    def fields(self, url: str) -> dict[str, str]:
        """The hidden CSRF and idempotency key fields a rendered form carries."""
        page = self.web.get(url).get_data(as_text=True)
        found = dict(
            re.findall(
                r'<input type="hidden" name="(csrf|key|expected_version)" value="([^"]*)"', page
            )
        )
        assert "csrf" in found, "CSRF form token missing"
        return found

    def submit(
        self, url: str, form: str | None = None, hidden: dict[str, str] | None = None, **data: Any
    ):
        """GET the form at `form` (default `url`) for its hidden fields, then POST `data` to `url`.

        `hidden` are hidden fields already taken from a form rendered earlier, for a form opened while the API was up.
        """
        return self.web.post(url, data={**(hidden or self.fields(form or url)), **data})

    def text(self, url: str) -> str:
        return self.web.get(url).get_data(as_text=True)

    def only_drive(self) -> dict[str, Any]:
        drives = self.api.drives()
        assert len(drives) == 1, drives
        return drives[0]
