from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jsonschema

from robot_doctor.demo import LAUNCH_PATH, example_files, example_zip, write_example
from robot_doctor.intake import extract_zip_upload
from robot_doctor.scanner import scan_repository
from robot_doctor.web import WebApplication, assessment_panel, home_page, page, project_name, result_body, task_page


class ShowcaseTests(unittest.TestCase):
    def test_only_the_reviewed_launch_reference_changes(self):
        broken, fixed = example_files("broken"), example_files("fixed")
        self.assertEqual([name for name in broken if broken[name] != fixed[name]], [LAUNCH_PATH])
        self.assertEqual(broken[LAUNCH_PATH].replace('"missing.yaml"', '"scan.yaml"'), fixed[LAUNCH_PATH])
        with self.assertRaises(ValueError):
            example_files("../../private")

    def test_downloaded_examples_reproduce_the_finding_and_correction(self):
        schema = json.loads((ROOT / "schemas/robot_doctor_scan.schema.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            for stage in ("broken", "fixed"):
                archive = Path(temporary) / f"{stage}.zip"
                archive.write_bytes(example_zip(stage))
                repository = extract_zip_upload(archive, Path(temporary) / stage)
                data = scan_repository(repository)
                jsonschema.Draft202012Validator(schema).validate(data)
                self.assertEqual([item["code"] for item in data["diagnostics"]], ["RD302"] if stage == "broken" else [])
                if stage == "broken":
                    finding = data["diagnostics"][0]
                    self.assertEqual(finding["evidence"][0]["file"], LAUNCH_PATH)
                    self.assertIn("missing.yaml", finding["message"])
                    self.assertIn("ament_python", finding["remediation"]["patch_hint"])
                    self.assertNotIn("install(DIRECTORY", finding["remediation"]["patch_hint"])
                self.assertEqual(data["summary"]["topics"], 1)
                self.assertIn("/scan", json.dumps(data["architecture"]))

    def test_example_is_cached_offline_and_downloads_use_regular_task_routes(self):
        application = WebApplication()
        try:
            with mock.patch("robot_doctor.web.clone_git_repository", side_effect=AssertionError("example must be offline")):
                broken = application.example("broken")
                fixed = application.example("fixed")
                self.assertIs(application.example("broken"), broken)
            rendered = task_page(broken, application.csrf_token)
            self.assertIn(f"/tasks/{broken.id}/result.json", rendered)
            self.assertIn("/example/broken.zip", rendered)
            self.assertIn("/example?stage=fixed", rendered)
            self.assertIn("deliberately introduced", rendered)
            self.assertIn("RD302", rendered)
            self.assertIn("No errors or warnings", task_page(fixed, application.csrf_token))
            self.assertNotIn("Review the flagged integration issues", task_page(fixed, application.csrf_token))
            self.assertEqual(len(application.tasks), 2)
        finally:
            application.close()

    def test_first_use_prioritizes_findings_and_retains_full_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_example(root, "broken")
            data = scan_repository(root)
        rendered = result_body(data)
        self.assertLess(rendered.index('id="assessment"'), rendered.index('id="findings"'))
        self.assertLess(rendered.index('id="findings"'), rendered.index('id="architecture"'))
        self.assertIn("Inspect scan_demo/launch/scan.launch.py", rendered)
        self.assertIn("<details open><summary>Source evidence", rendered)
        self.assertIn("Complete scan inventory", rendered)
        self.assertNotIn("<details open><summary>Nodes", rendered)
        filtered = result_body(data, severity_filter="warning")
        self.assertIn("No findings match these filters", filtered)
        self.assertNotIn("<h3>No static findings", filtered)
        self.assertIn("Explore an example", home_page("token"))
        self.assertIn('role="alert"', home_page("token", "Choose a source"))
        self.assertIn("No errors or warnings", assessment_panel({"diagnostics": []}))

    def test_project_and_error_text_are_escaped(self):
        name = project_name({"repository": {"source": "https://github.com/team/my-robot.git"}})
        self.assertEqual(name, "my-robot")
        self.assertIn("&lt;script&gt;", page("<script>", ""))
        self.assertNotIn("<script>", home_page("token", "<script>"))
        self.assertNotIn("<img src=x>", assessment_panel({"diagnostics": [{"severity": "error", "title": "<img src=x>"}]}))

    def test_offline_export_contains_working_relative_links_and_real_scans(self):
        spec = importlib.util.spec_from_file_location("build_showcase", ROOT / "tools/build_showcase.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            module.build_showcase(output)
            home = (output / "index.html").read_text()
            for folder in ("public_case", "moveit_case"):
                self.assertIn(f'href="{folder}/index.html"', home)
                self.assertIn('href="../index.html">Choose another public issue case', (output / folder / "index.html").read_text())
            for stage in ("broken", "fixed"):
                rendered = (output / f"{stage}.html").read_text()
                self.assertIn('href="broken.html"', rendered)
                self.assertIn('href="fixed.html"', rendered)
                self.assertIn(f'href="{stage}.zip"', rendered)
                self.assertNotIn('<form', rendered)
                self.assertNotIn('/private/', rendered)
                self.assertNotIn('/Users/', rendered)
                with zipfile.ZipFile(io.BytesIO((output / f"{stage}.zip").read_bytes())) as archive:
                    self.assertIn(LAUNCH_PATH, archive.namelist())
            class Links(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.references = []
                    self.identifiers = set()

                def handle_starttag(self, tag, attrs):
                    values = dict(attrs)
                    if "id" in values:
                        self.identifiers.add(values["id"])
                    self.references.extend(values[key] for key in ("href", "src") if key in values)

            documents = {}
            for document in output.rglob("*.html"):
                parsed = Links()
                parsed.feed(document.read_text())
                documents[document.resolve()] = parsed
            for document, parsed in documents.items():
                for reference in parsed.references:
                    url = urlparse(reference)
                    if url.scheme:
                        self.assertEqual(url.scheme, "https", reference)
                        self.assertEqual(url.netloc, "github.com", reference)
                        continue
                    target = (document.parent / unquote(url.path)).resolve() if url.path else document
                    self.assertTrue(target.is_file(), reference)
                    if url.fragment:
                        self.assertIn(url.fragment, documents[target].identifiers, reference)


if __name__ == "__main__":
    unittest.main()
