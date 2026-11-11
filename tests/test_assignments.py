"""Synthetic HTTP/SQLite scenarios; only the external Project reader is replaced."""

import json
import tempfile
import unittest
from pathlib import Path

from test_inventory import token

from app import create_app

PROJECT = "t6p_" + "1" * 32
OLD = "a" * 32
NEW = "b" * 32


def export(page=OLD, legacy=(), name="Synthetic show"):
    return {
        "complete": True,
        "projects": [
            {
                "t6_id": PROJECT,
                "code": "SYN",
                "name": name,
                "active_page_id": page,
                "legacy_page_ids": list(legacy),
            }
        ],
    }


class Reader:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class AssignmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "synthetic.sqlite")
        self.reader = Reader(export())
        self.app = create_app(self.path, project_reader=self.reader)
        self.client = self.app.test_client()
        self.csrf = token(self.client)
        self.location = self.client.post(
            "/new",
            data={
                "csrf": self.csrf,
                "description": "synthetic uncertain drive",
                "needs_review": "on",
            },
        ).headers["Location"]

    def refresh(self):
        return self.app.test_cli_runner().invoke(args=["refresh-projects"])

    def add(self, **data):
        return self.client.post(self.location + "/assignments", data={"csrf": self.csrf, **data})

    def test_explicit_end_validation_history_and_independent_open_use(self):
        import re

        self.assertEqual(self.refresh().exit_code, 0)
        self.assertEqual(
            self.add(
                project_page=OLD,
                label="reused label",
                start="2024-03-10",
                remarks="<script>synthetic</script>",
            ).status_code,
            303,
        )
        self.assertEqual(
            self.add(project_text="unmatched show", label="reused label").status_code, 303
        )
        page = self.client.get(self.location).text
        keys = re.findall(r'data-assignment-id="(\d+)"', page)
        self.assertEqual(len(keys), 2)
        self.assertIn("Role: Unknown", page)
        self.assertIn("Start: Unknown", page)
        self.assertIn("not physical custody", page)
        self.assertNotIn("<script>synthetic</script>", page)
        first = re.search(r'action="([^"]+/end)"', page[page.index("Start: 2024-03-10") :])
        assert first is not None
        end_url = first.group(1)
        for end in ("", "not-date", "2024-02-30", "2024-03-09"):
            self.assertEqual(
                self.client.post(end_url, data={"csrf": self.csrf, "end": end}).status_code, 400
            )
            self.assertEqual(self.client.get(self.location).text, page)
        self.assertEqual(
            self.client.post(end_url, data={"csrf": self.csrf, "end": "2024-03-11"}).status_code,
            303,
        )
        ended = self.client.get(self.location).text
        self.assertIn("End: 2024-03-11", ended)
        self.assertEqual(ended.count("End: Open"), 1)
        self.assertEqual(ended.count("Label: reused label"), 2)
        self.assertNotIn(self.location, self.client.get("/?project=" + PROJECT).text)
        self.assertIn(self.location, self.client.get("/?project_text=unmatched%20show").text)
        self.assertEqual(
            self.client.post(end_url, data={"csrf": self.csrf, "end": "2024-04-01"}).status_code,
            409,
        )
        self.assertEqual(
            create_app(self.path).test_client().get(self.location).text.count("End: 2024-03-11"), 1
        )
        for fields in (
            {"project_text": "x", "start": "2024-03-10", "end": "2024-03-09"},
            {"project_text": "x", "role": "invented"},
            {"project_page": PROJECT},
            {"project_page": OLD, "project_text": "conflict"},
            {},
        ):
            self.assertEqual(self.add(**fields).status_code, 400)
        before = self.client.get(self.location).text
        self.assertEqual(
            self.client.post(
                "/drive/unknown/assignments", data={"csrf": self.csrf, "project_text": "x"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/drive/unknown/assignments/1/end", data={"csrf": self.csrf, "end": "2024-03-12"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get(self.location).text, before)
        self.assertEqual(self.client.get(end_url).status_code, 405)
        self.assertEqual(self.client.get(self.location + "/assignments").status_code, 405)
        self.assertEqual(self.client.post(end_url, data={"end": "2024-03-12"}).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.location + "/assignments",
                data={"csrf": self.csrf, "project_text": "x"},
                headers={"Origin": "http://evil.test"},
            ).status_code,
            403,
        )

    def test_bootstrap_refresh_outage_legacy_and_restart(self):
        initial = Path(self.temp.name) / "authorized-synthetic-export.json"
        initial.write_text(json.dumps(export()))
        result = self.app.test_cli_runner().invoke(
            args=["refresh-projects", "--export", str(initial)]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Synthetic show", self.client.get("/").text)
        self.assertEqual(self.add(project_page=OLD, label="old synthetic label").status_code, 303)
        self.reader.payload = export(NEW, (OLD,), "Renamed synthetic show")
        self.assertEqual(self.refresh().exit_code, 0)
        self.assertIn(self.location, self.client.get("/?project=" + PROJECT).text)
        self.assertIn("Renamed synthetic show", self.client.get(self.location).text)
        for payload in (
            RuntimeError("private upstream secret"),
            {"complete": False, "projects": []},
            export(NEW),
            export(OLD, name="invalid rollback"),
        ):
            self.reader.payload = payload
            self.assertNotEqual(self.refresh().exit_code, 0)
            page = self.client.get("/").text
            self.assertIn("Refresh failed", page)
            self.assertIn("Last complete cache", page)
            self.assertIn("Renamed synthetic show", page)
            self.assertNotIn("private upstream secret", page)
        self.assertEqual(self.add(project_text="SYN", label="unresolved label").status_code, 303)
        reopened = create_app(self.path).test_client()
        page = reopened.get(self.location).text
        self.assertIn("Unresolved: SYN", page)
        self.assertIn("old synthetic label", page)
        self.assertIn("Refresh failed", reopened.get("/").text)
        self.assertIn(self.location, reopened.get("/?project=" + PROJECT).text)
        self.assertEqual(
            self.client.post("/refresh-projects", data={"csrf": self.csrf}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
