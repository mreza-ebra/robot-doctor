from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

WORKSPACE = Path(__file__).parents[1]
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor.config import ScanConfig
from robot_doctor import web as web_module
from robot_doctor.intake import IntakeError, clone_git_repository, directory_size_exceeds, extract_zip_upload, validate_git_url
from robot_doctor.scanner import scan_repository
from robot_doctor.web import WebApplication, allowed_bind_host, architecture_visual, home_page, is_loopback_host, result_body, task_page, valid_loopback_host_header, valid_origin


class SelfServiceTests(unittest.TestCase):
    def test_git_url_validation_rejects_local_and_credentialed_sources(self):
        self.assertEqual(validate_git_url("https://github.com/ros2/examples.git"), "https://github.com/ros2/examples.git")
        for value in ("file:///tmp/repository", "https://localhost/repository.git", "https://user:secret@example.com/repository.git"):
            with self.assertRaises(IntakeError):
                validate_git_url(value)

    def test_zip_extraction_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape.txt", "unsafe")
            with self.assertRaises(IntakeError):
                extract_zip_upload(archive, Path(directory) / "repository")

    def test_zip_extraction_rejects_repository_git_metadata(self):
        for member in (".git/config", "nested/.GIT/config", "nested/.git /config"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "unsafe.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(member, "[core]\nfsmonitor = /tmp/untrusted-hook\n")
                with self.assertRaisesRegex(IntakeError, "forbidden Git metadata"):
                    extract_zip_upload(archive, Path(directory) / "repository")

    def test_checkout_size_limit_stops_git_clone(self):
        class FakeProcess:
            returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repository"

            def fake_popen(*args, **kwargs):
                destination.mkdir()
                (destination / "large.bin").write_bytes(b"x" * 2_000)
                return FakeProcess()

            with mock.patch("robot_doctor.intake.subprocess.Popen", side_effect=fake_popen):
                with self.assertRaisesRegex(IntakeError, "checkout exceeded"):
                    clone_git_repository("https://github.com/example/repository.git", destination, max_checkout_bytes=1_000)

            self.assertEqual(directory_size_exceeds(destination, 1_000), (True, 2_000))

    def test_private_git_token_is_not_written_to_clone_arguments(self):
        class CompleteProcess:
            returncode = 0

            def poll(self):
                return 0

        captured = {}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repository"

            def fake_popen(command, **kwargs):
                captured["command"] = command
                captured["environment"] = kwargs["env"]
                destination.mkdir()
                return CompleteProcess()

            with mock.patch("robot_doctor.intake.subprocess.Popen", side_effect=fake_popen):
                clone_git_repository(
                    "https://github.com/example/private.git",
                    destination,
                    access_token="private-token-value",
                )

        self.assertNotIn("private-token-value", " ".join(captured["command"]))
        self.assertEqual(captured["environment"]["GIT_CONFIG_KEY_0"], "http.extraHeader")
        self.assertNotIn("private-token-value", captured["environment"]["GIT_CONFIG_VALUE_0"])
        self.assertEqual(captured["environment"]["GIT_TERMINAL_PROMPT"], "0")

    def test_web_security_and_task_quota(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(valid_loopback_host_header("localhost:8765"))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(valid_loopback_host_header("robot-doctor.example"))
        self.assertTrue(valid_origin("http://127.0.0.1:8765", "127.0.0.1:8765"))
        self.assertTrue(valid_origin("http://localhost:8765", "127.0.0.1:8765"))
        self.assertTrue(valid_origin("http://127.0.0.1:8765", "localhost:8765"))
        self.assertTrue(valid_origin("null", "127.0.0.1:8765"))
        self.assertFalse(valid_origin("null", "robot-doctor.example:8765"))
        self.assertFalse(valid_origin("http://localhost:8766", "127.0.0.1:8765"))
        self.assertFalse(valid_origin("https://attacker.example", "127.0.0.1:8765"))
        self.assertFalse(allowed_bind_host("0.0.0.0"))
        with mock.patch.dict("os.environ", {"ROBOT_DOCTOR_CONTAINER": "1"}):
            self.assertTrue(allowed_bind_host("0.0.0.0"))

        application = WebApplication(max_concurrent_tasks=1)
        try:
            application._new_task("first", "upload", ScanConfig())
            with self.assertRaisesRegex(IntakeError, "task limit"):
                application._new_task("second", "upload", ScanConfig())
            page = home_page(application.csrf_token)
            self.assertIn(application.csrf_token, page)
            self.assertIn("Scan repository", page)
            self.assertIn('name="git_token"', page)
            self.assertIn('type="password"', page)
            self.assertIn("Advanced options", page)
        finally:
            application.close()

        with mock.patch.object(sys, "argv", ["robot-doctor-web", "--host", "0.0.0.0", "--no-browser"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    web_module.main()
        self.assertEqual(exit_context.exception.code, 2)

    def test_upload_scan_button_flow_generates_results(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr(
                "sample/package.xml",
                """<package format="3"><name>sample</name><version>0.1.0</version><description>sample</description><maintainer email="sample@example.com">Sample</maintainer><license>MIT</license><buildtool_depend>ament_cmake</buildtool_depend><export><build_type>ament_cmake</build_type></export></package>""",
            )
            archive.writestr(
                "sample/CMakeLists.txt",
                "cmake_minimum_required(VERSION 3.8)\nproject(sample)\nfind_package(ament_cmake REQUIRED)\nament_package()\n",
            )
        application = WebApplication()
        try:
            task = application.submit_upload("sample.zip", payload.getvalue(), ScanConfig())
            task.thread.join(timeout=10)
            self.assertEqual(task.status, "complete", task.error)
            self.assertEqual(task.summary["packages"], 1)
            result = json.loads((task.directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["provenance"]["input"]["source_type"], "upload")
            self.assertEqual(result["provenance"]["input"]["archive_sha256"], hashlib.sha256(payload.getvalue()).hexdigest())
            self.assertRegex(result["provenance"]["input"]["content_sha256"], "^[a-f0-9]{64}$")
            self.assertTrue((task.directory / "result.json").is_file())
            self.assertTrue((task.directory / "result.html").is_file())
            self.assertTrue((task.directory / "basic.md").is_file())
            self.assertIn("Scan repository", home_page(application.csrf_token))
            rendered = task_page(task, application.csrf_token)
            self.assertIn("Prioritized Findings", rendered)
            self.assertIn("Node and interface topology", rendered)
            self.assertIn("Sensors, algorithms, and actuation", rendered)
            self.assertIn("Modification points", rendered)
            self.assertIn("Reproducibility", rendered)
            self.assertIn('name="severity"', rendered)
            self.assertIn('name="package"', rendered)
            self.assertIn("result.json", rendered)
        finally:
            application.close()

    def test_topology_and_findings_filters_use_real_entities(self):
        data = scan_repository(WORKSPACE / "tests" / "fixtures" / "python_robot")
        diagram = architecture_visual(data)
        rendered = result_body(data, "/tasks/example/", severity_filter="info", package_filter="probe_py")

        self.assertIn("Node and interface topology", diagram)
        self.assertIn("probe_node", diagram)
        self.assertIn("status", diagram)
        self.assertNotIn("Detected package structure", diagram)
        self.assertIn('<option value="info" selected>', rendered)
        self.assertIn('<option value="probe_py" selected>', rendered)
        self.assertIn('<details class="finding finding-info">', rendered)

    def test_docker_one_command_assets_are_local_only(self):
        compose = (WORKSPACE / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (WORKSPACE / "Dockerfile").read_text(encoding="utf-8")
        launcher = (WORKSPACE / "start_robot_doctor.command").read_text(encoding="utf-8")
        manifest = (WORKSPACE / "MANIFEST.in").read_text(encoding="utf-8")

        self.assertIn('127.0.0.1:8765:8765', compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("ROBOT_DOCTOR_CONTAINER=1", dockerfile)
        self.assertIn("docker compose up --build -d", launcher)
        self.assertIn("include compose.yaml", manifest)
        self.assertIn("include start_robot_doctor.command", manifest)


if __name__ == "__main__":
    unittest.main()
