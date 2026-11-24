"""Projects and movement pages backed by workbook reloads through the real API."""

import re
from datetime import datetime
from urllib.parse import urlencode

from openpyxl import Workbook  # type: ignore[import-untyped]
from support import DriveAppCase
from t6.verbs import (
    Done,
    DriveProjectsInput,
    DriveReloadInput,
    Preview,
)


def load_projects(case: DriveAppCase) -> str:
    project = case.api.project("Synthetic violet project")
    case.api.project("Synthetic empty project")
    client = case.api.client()
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in [
        (
            "Synthetic violet",
            [("Synthetic master", "SYN-MASTER"), ("Synthetic backup", "SYN-BACKUP")],
        ),
        ("Synthetic text project", [("Synthetic text drive", "SYN-TEXT")]),
    ]:
        sheet = workbook.create_sheet(name)
        sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER"])
        for row in rows:
            sheet.append(row)
    path = case.directory + "/projects.xlsx"
    workbook.save(path)
    tracker = Workbook()
    sheet = tracker.active
    sheet.title = "NOVEMBER 2026"
    sheet.append(["DATE", "PROJECT NAME", "LABEL", "IN/OUT", "RECEIVED BY", "REMARKS"])
    for movement_row in [
        ("2026-11-01", "Synthetic violet", "Synthetic master", "OUT", "Invented Courier", "older"),
        ("2026-11-03", "Synthetic violet", "Synthetic missing", "IN", "Invented Receiver", "newer"),
        (
            "2026-11-02",
            "Synthetic text project",
            "Synthetic text drive",
            "IN",
            "Invented Courier",
            "text",
        ),
    ]:
        sheet.append([datetime.fromisoformat(movement_row[0]), *movement_row[1:]])
    tracker_path = case.directory + "/tracker.xlsx"
    tracker.save(tracker_path)
    preview = client.drive_reload(
        DriveReloadInput(workbook=path, tracker=tracker_path, idempotency_key="reload")
    )
    assert isinstance(preview, Preview), preview
    done = client.drive_reload(
        DriveReloadInput(
            workbook=path, tracker=tracker_path, idempotency_key="reload", confirm=preview.token
        )
    )
    assert isinstance(done, Done), done
    assert done.result["movements_read"] == 3, done
    return project


class ProjectTests(DriveAppCase):
    def test_projects_show_api_counts_including_text_projects(self):
        load_projects(self)
        counts = self.api.read("drive_projects", DriveProjectsInput)["projects"]
        self.assertEqual(
            sorted((p["total"], p["in_house"], p["released"]) for p in counts),
            [(1, 1, 0), (2, 1, 1)],
        )
        response = self.web.get("/projects")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for item in counts:
            ref = item["project"]
            key = ref["page_id"] or ref["text"]
            row = re.search(
                r'<li data-project="' + re.escape(key) + r'">(.*?)</li>', html, re.DOTALL
            )
            self.assertIsNotNone(row)
            assert row is not None
            for name in ("total", "in_house", "released"):
                self.assertIn(f'data-count="{name}">{item[name]}</span>', row.group(1))
        self.assertNotIn("Synthetic empty project", html)
        self.assertIn('href="/projects"', self.text("/"))
        self.assertIn("Practice: records are wiped", html)

    def test_movements_filters_and_unmatched_rows(self):
        load_projects(self)
        response = self.web.get("/movements")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Unmatched", html)
        self.assertIn("Synthetic missing", html)
        self.assertIn("Synthetic master", html)
        self.assertLess(html.index("2026-11-03"), html.index("2026-11-02"))
        self.assertLess(html.index("2026-11-02"), html.index("2026-11-01"))
        for filters, included, excluded in [
            ({"project": "Synthetic violet"}, "Synthetic master", "Synthetic text drive"),
            ({"person": "Receiver"}, "Synthetic missing", "Synthetic master"),
            ({"q": "older"}, "Synthetic master", "Synthetic missing"),
            (
                {"start": "2026-11-02", "end": "2026-11-02"},
                "Synthetic text drive",
                "Synthetic master",
            ),
            (
                {
                    "project": "Synthetic violet",
                    "person": "Courier",
                    "q": "older",
                    "start": "2026-11-01",
                    "end": "2026-11-01",
                },
                "Synthetic master",
                "Synthetic missing",
            ),
        ]:
            with self.subTest(filters=filters):
                html = self.text("/movements?" + urlencode(filters))
                self.assertIn(included, html)
                self.assertNotIn(excluded, html)
        self.assertIn("No movements", self.text("/movements?q=absent"))

    def test_project_page_lists_its_drives_and_newest_movements(self):
        load_projects(self)
        for project, label, serial, excluded in [
            ("Synthetic violet", "Synthetic master", "SYN-MASTER", "Synthetic text drive"),
            ("Synthetic text project", "Synthetic text drive", "SYN-TEXT", "Synthetic master"),
        ]:
            with self.subTest(project=project):
                response = self.web.get("/projects/view?" + urlencode({"project": project}))
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn(label, html)
                self.assertIn(serial, html)
                self.assertNotIn(excluded, html)
                self.assertIn("in custody" if project.endswith("project") else "released", html)
                self.assertIn("Movement log", html)
                self.assertIn("Invented Courier", html)
                if project == "Synthetic violet":
                    self.assertIn("released", html)
                    self.assertIn("Synthetic missing", html)
                    self.assertLess(html.index("2026-11-03"), html.index("2026-11-01"))


class ProjectSearchTests(DriveAppCase):
    """Search, filters and sorts of the Projects page, from the API's counts."""

    def names(self, query: str = "") -> list[str]:
        """The Projects shown, in order, as the page prints them."""
        html = self.text("/projects" + query)
        return re.findall(
            r'<li data-project="[^"]*"><article class="card">\s*<h2><a [^>]*>([^<]*)</a>', html
        )

    def build(self) -> None:
        load_projects(self)  # violet: 2 drives, 1 out; text project: 1 drive, name only
        alpha = self.api.project("Synthetic alpha")
        for number in range(3):
            drive = self.api.add_drive(label=f"Alpha {number}", serial=f"SYN-ALPHA-{number}")
            self.api.assign(drive, alpha, f"A{number}", "backup")

    def test_search_finds_a_project_by_part_of_its_name_in_any_case(self):
        self.build()
        self.assertEqual(self.names("?q=ALPH"), ["Synthetic alpha"])
        self.assertEqual(self.names("?q=text"), ["Unresolved: Synthetic text project"])
        self.assertEqual(self.names("?q=nothing-like-this"), [])
        self.assertIn("No Projects match", self.text("/projects?q=nothing-like-this"))
        self.assertEqual(len(self.names("?q=synthetic")), 3)

    def test_search_finds_a_project_by_its_code(self):
        from t6.notion.testing import title

        page = self.api.notion.add_page("projects", {"Name": title("Synthetic coded")})
        self.api.registry.register(
            actor="setup", idempotency_key="coded", notion_page_id=page["id"], code="ZQX-77"
        )
        drive = self.api.add_drive(label="Coded drive", serial="SYN-CODED")
        from t6.verbs import ProjectListInput

        registered = self.api.read("project_list", ProjectListInput)["projects"]
        project = next(p["t6_id"] for p in registered if p["name"] == "Synthetic coded")
        self.api.assign(drive, project, "C1", "backup")
        self.assertEqual(self.names("?q=zqx"), ["Synthetic coded"])
        self.assertEqual(self.names("?q=Synthetic+coded"), ["Synthetic coded"])

    def test_filters_narrow_the_list_and_combine(self):
        self.build()
        self.assertEqual(self.names("?out=1"), ["Unresolved: Synthetic violet"])
        self.assertEqual(self.names("?kind=registered"), ["Synthetic alpha"])
        self.assertEqual(
            sorted(self.names("?kind=name_only")),
            ["Unresolved: Synthetic text project", "Unresolved: Synthetic violet"],
        )
        self.assertEqual(self.names("?kind=name_only&out=1"), ["Unresolved: Synthetic violet"])
        self.assertEqual(self.names("?kind=registered&out=1"), [])
        self.assertEqual(
            self.names("?kind=name_only&q=text"), ["Unresolved: Synthetic text project"]
        )

    def test_sorts_order_the_list_as_named(self):
        self.build()
        plain = lambda query: [n.removeprefix("Unresolved: ") for n in self.names(query)]
        self.assertEqual(self.names(), self.names("?sort=latest"))
        self.assertEqual(
            plain("?sort=az"), ["Synthetic alpha", "Synthetic text project", "Synthetic violet"]
        )
        self.assertEqual(
            plain("?sort=total"), ["Synthetic alpha", "Synthetic violet", "Synthetic text project"]
        )
        self.assertEqual(plain("?sort=out")[0], "Synthetic violet")

    def test_latest_movement_is_the_default_sort_newest_first_with_no_movement_last(self):
        self.build()
        # Alpha goes out on a recording post: a movement on 2026-11-20, newer than violet's 2026-11-03.
        self.api.capture("OUT Invented Courier|SYN-ALPHA-0", day="2026-11-20")
        plain = lambda query: [n.removeprefix("Unresolved: ") for n in self.names(query)]
        self.assertEqual(
            plain(""), ["Synthetic alpha", "Synthetic violet", "Synthetic text project"]
        )
        self.assertEqual(plain(""), plain("?sort=latest"))
        self.assertIn('value="latest" selected', self.text("/projects"))
        self.assertIn("Latest movement", self.text("/projects"))

    def test_the_other_sorts_still_work_beside_the_default(self):
        self.build()
        self.assertEqual(
            [n.removeprefix("Unresolved: ") for n in self.names("?sort=az")],
            ["Synthetic alpha", "Synthetic text project", "Synthetic violet"],
        )

    def test_an_unknown_sort_or_filter_value_is_ignored_and_the_form_keeps_what_was_typed(self):
        self.build()
        self.assertEqual(len(self.names("?sort=bogus&kind=bogus")), 3)
        page = self.text("/projects?q=alph&out=1&kind=registered&sort=total")
        for kept in ('value="alph"', 'name="out" value="1" checked', 'value="total" selected'):
            self.assertIn(kept, page)
