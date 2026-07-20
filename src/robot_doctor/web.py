from __future__ import annotations

import argparse
import html
import hmac
import ipaddress
import json
import secrets
import socket
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ConfigError, ScanConfig
from .intake import DEFAULT_MAX_CHECKOUT_BYTES, IntakeError, clone_git_repository, extract_zip_upload
from .overviews import basic_document, expert_document, intermediate_document
from .scanner import ScanCancelled, scan_repository

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_CONCURRENT_TASKS = 2
ACTIVE_TASK_STATUSES = {"queued", "intake", "scanning"}


@dataclass
class ScanTask:
    id: str
    source: str
    source_type: str
    directory: Path
    config: ScanConfig
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=lambda: {"stage": "queued", "current": 0, "total": 1, "message": "Waiting to start"})
    error: str | None = None
    summary: dict[str, Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class WebApplication:
    def __init__(
        self,
        *,
        max_concurrent_tasks: int = DEFAULT_MAX_CONCURRENT_TASKS,
        max_checkout_bytes: int = DEFAULT_MAX_CHECKOUT_BYTES,
    ) -> None:
        if max_concurrent_tasks <= 0:
            raise ValueError("max_concurrent_tasks must be positive")
        if max_checkout_bytes <= 0:
            raise ValueError("max_checkout_bytes must be positive")
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="robot-doctor-web-")
        self.root = Path(self.temporary_directory.name)
        self.tasks: dict[str, ScanTask] = {}
        self.lock = threading.Lock()
        self.max_concurrent_tasks = max_concurrent_tasks
        self.max_checkout_bytes = max_checkout_bytes
        self.csrf_token = secrets.token_urlsafe(32)

    def close(self) -> None:
        for task in self.tasks.values():
            task.cancel_event.set()
        for task in self.tasks.values():
            if task.thread and task.thread.is_alive():
                task.thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def submit_git(self, url: str, config: ScanConfig) -> ScanTask:
        task = self._new_task(url, "git", config)
        task.thread = threading.Thread(target=self._run_git_task, args=(task,), daemon=True)
        task.thread.start()
        return task

    def submit_upload(self, filename: str, payload: bytes, config: ScanConfig) -> ScanTask:
        if len(payload) > MAX_UPLOAD_BYTES:
            raise IntakeError(f"upload exceeds the {MAX_UPLOAD_BYTES}-byte limit")
        task = self._new_task(filename or "repository.zip", "upload", config)
        archive = task.directory / "upload.zip"
        archive.write_bytes(payload)
        task.thread = threading.Thread(target=self._run_upload_task, args=(task, archive), daemon=True)
        task.thread.start()
        return task

    def _new_task(self, source: str, source_type: str, config: ScanConfig) -> ScanTask:
        with self.lock:
            active_tasks = sum(task.status in ACTIVE_TASK_STATUSES for task in self.tasks.values())
            if active_tasks >= self.max_concurrent_tasks:
                raise IntakeError(
                    f"the local task limit of {self.max_concurrent_tasks} active scan(s) has been reached"
                )
            identifier = uuid.uuid4().hex
            directory = self.root / identifier
            directory.mkdir()
            task = ScanTask(identifier, source, source_type, directory, config)
            self.tasks[identifier] = task
        return task

    def _run_git_task(self, task: ScanTask) -> None:
        try:
            self._update(task, status="intake", progress={"stage": "clone", "current": 0, "total": 1, "message": "Cloning repository"})
            repository = clone_git_repository(
                task.source,
                task.directory / "repository",
                cancel_check=task.cancel_event.is_set,
                max_checkout_bytes=self.max_checkout_bytes,
            )
            self._scan(task, repository)
        except (IntakeError, ConfigError, OSError) as exc:
            self._fail(task, str(exc))
        except ScanCancelled:
            self._update(task, status="cancelled", progress={"stage": "cancelled", "current": 0, "total": 1, "message": "Cancelled"})
        except Exception as exc:
            self._fail(task, f"unexpected scan failure: {exc}")

    def _run_upload_task(self, task: ScanTask, archive: Path) -> None:
        try:
            self._update(task, status="intake", progress={"stage": "extract", "current": 0, "total": 1, "message": "Extracting ZIP archive"})
            repository = extract_zip_upload(archive, task.directory / "repository")
            self._scan(task, repository)
        except (IntakeError, ConfigError, OSError) as exc:
            self._fail(task, str(exc))
        except ScanCancelled:
            self._update(task, status="cancelled", progress={"stage": "cancelled", "current": 0, "total": 1, "message": "Cancelled"})
        except Exception as exc:
            self._fail(task, f"unexpected scan failure: {exc}")

    def _scan(self, task: ScanTask, repository: Path) -> None:
        self._update(task, status="scanning")

        def progress(event: dict[str, Any]) -> None:
            self._update(task, progress=event)

        data = scan_repository(repository, config=task.config, progress=progress, cancel_check=task.cancel_event.is_set)
        data["repository"].update(source=task.source, source_type=task.source_type)
        (task.directory / "result.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        documents = {
            "basic.md": basic_document(repository, data),
            "intermediate.md": intermediate_document(repository, data),
            "expert.md": expert_document(repository, data),
        }
        for filename, content in documents.items():
            (task.directory / filename).write_text(content.rstrip() + "\n", encoding="utf-8")
        self._update(task, status="complete", summary=data["summary"], progress={"stage": "complete", "current": 1, "total": 1, "message": "Complete"})

    def _fail(self, task: ScanTask, error: str) -> None:
        self._update(task, status="failed", error=error, progress={"stage": "failed", "current": 0, "total": 1, "message": error})

    def _update(self, task: ScanTask, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(task, key, value)


def page(title: str, body: str, *, refresh: int | None = None) -> str:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">{refresh_tag}<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#172033}}fieldset{{border:1px solid #ccd4e0;border-radius:10px;padding:1rem;margin:1rem 0}}input,select,button{{font:inherit;padding:.65rem;margin:.25rem 0}}input[type=text],input[type=url]{{width:min(100%,700px)}}button{{background:#2156d9;color:white;border:0;border-radius:7px;cursor:pointer}}.muted{{color:#596579}}.error{{color:#a21b1b}}table{{border-collapse:collapse}}td,th{{border:1px solid #d8dee8;padding:.45rem;text-align:left}}pre{{white-space:pre-wrap;background:#f5f7fa;padding:1rem;border-radius:8px;overflow:auto}}
</style></head><body><h1>{html.escape(title)}</h1>{body}</body></html>"""


def home_page(csrf_token: str, error: str | None = None) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return page(
        "Robot Doctor",
        f"""<p>Upload a ROS 2 repository ZIP or provide a public HTTPS Git URL, then press <strong>Scan</strong>.</p>{error_html}
<form action="/scan" method="post" enctype="multipart/form-data">
<input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
<fieldset><legend>Repository</legend><label>Git URL<br><input type="url" name="git_url" placeholder="https://github.com/owner/repository.git"></label><p class="muted">or</p><label>Repository ZIP<br><input type="file" name="repository_zip" accept=".zip,application/zip"></label></fieldset>
<fieldset><legend>Noise controls</legend><label>Dependency diagnostics <select name="dependency_mode"><option value="direct">Direct references (recommended)</option><option value="off">Off</option><option value="all">All inferred references</option></select></label><br><label>Suppress codes<br><input type="text" name="suppress_codes" placeholder="RD101,RD202"></label></fieldset>
<button type="submit">Scan repository</button></form>
<p class="muted">Runs locally on this computer. Results are temporary and disappear when this application closes. ZIP uploads are size-limited and safely extracted. Dynamic ROS runtime behavior still requires optional runtime verification.</p>""",
    )


def task_page(task: ScanTask, csrf_token: str) -> str:
    progress = task.progress
    total = progress.get("total") or 1
    percent = min(100, int(100 * progress.get("current", 0) / total))
    body = f'<p><strong>Status:</strong> {html.escape(task.status)}</p><p>{html.escape(progress.get("message") or "")}</p><progress max="100" value="{percent}">{percent}%</progress>'
    refresh = 2 if task.status in {"queued", "intake", "scanning"} else None
    if refresh:
        body += (
            f'<form action="/tasks/{task.id}/cancel" method="post">'
            f'<input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">'
            '<button type="submit">Cancel</button></form>'
        )
    elif task.status == "complete":
        summary_rows = "".join(f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>" for key, value in (task.summary or {}).items())
        body += f'<h2>Summary</h2><table>{summary_rows}</table><h2>Results</h2><p><a href="/tasks/{task.id}/result.json">JSON</a> · <a href="/tasks/{task.id}/basic.md">Basic</a> · <a href="/tasks/{task.id}/intermediate.md">Intermediate</a> · <a href="/tasks/{task.id}/expert.md">Expert</a></p>'
    elif task.error:
        body += f'<p class="error">{html.escape(task.error)}</p>'
    body += '<p><a href="/">Start another scan</a></p>'
    return page(f"Scan {task.id[:8]}", body, refresh=refresh)


def parse_multipart(content_type: str, payload: bytes) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", errors="replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + payload
    )
    fields: dict[str, str] = {}
    upload = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        value = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            upload = (filename, value)
        else:
            fields[name] = value.decode(part.get_content_charset() or "utf-8", errors="replace")
    return fields, upload


def is_loopback_host(host: str) -> bool:
    value = host.strip().strip("[]").rstrip(".").lower()
    if value in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def valid_loopback_host_header(host_header: str | None) -> bool:
    if not host_header:
        return False
    try:
        parsed = urlparse(f"//{host_header}")
        parsed.port
    except ValueError:
        return False
    return bool(parsed.hostname and is_loopback_host(parsed.hostname) and not parsed.username and not parsed.password)


def valid_origin(origin: str | None, host_header: str | None) -> bool:
    if not origin:
        return True
    if not host_header:
        return False
    parsed = urlparse(origin)
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and parsed.netloc.casefold() == host_header.strip().casefold()
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


class RequestHandler(BaseHTTPRequestHandler):
    server: "RobotDoctorHTTPServer"

    def do_GET(self) -> None:
        if not valid_loopback_host_header(self.headers.get("Host")):
            self.send_error(HTTPStatus.FORBIDDEN, "loopback Host header required")
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(home_page(self.server.application.csrf_token))
            return
        if parsed.path == "/healthz":
            self._bytes(b"ok\n", "text/plain; charset=utf-8")
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "tasks":
            task = self.server.application.tasks.get(parts[1])
            if not task:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 2:
                self._html(task_page(task, self.server.application.csrf_token))
                return
            filename = parts[2]
            if filename not in {"result.json", "basic.md", "intermediate.md", "expert.md"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            path = task.directory / filename
            if task.status != "complete" or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = "application/json" if filename.endswith(".json") else "text/markdown; charset=utf-8"
            self._bytes(path.read_bytes(), content_type, attachment=filename)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not valid_loopback_host_header(self.headers.get("Host")):
            self.send_error(HTTPStatus.FORBIDDEN, "loopback Host header required")
            return
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel":
            try:
                fields = self._read_urlencoded_form(16_384)
                self._validate_post(fields)
                task = self.server.application.tasks.get(parts[1])
                if not task:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                task.cancel_event.set()
                self._redirect(f"/tasks/{task.id}")
            except (IntakeError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path != "/scan":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD_BYTES + 1_000_000:
                raise IntakeError("request is empty or exceeds the upload limit")
            payload = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                fields, upload = parse_multipart(content_type, payload)
            else:
                fields = {key: values[0] for key, values in parse_qs(payload.decode("utf-8")).items()}
                upload = None
            self._validate_post(fields)
            suppressions = frozenset(code.strip() for code in fields.get("suppress_codes", "").split(",") if code.strip())
            config = ScanConfig(dependency_mode=fields.get("dependency_mode", "direct"), suppress_diagnostics=suppressions)
            config.validate()
            if upload and upload[1]:
                task = self.server.application.submit_upload(upload[0], upload[1], config)
            elif fields.get("git_url", "").strip():
                task = self.server.application.submit_git(fields["git_url"].strip(), config)
            else:
                raise IntakeError("provide either a Git URL or a ZIP archive")
            self._redirect(f"/tasks/{task.id}")
        except (IntakeError, ConfigError, ValueError) as exc:
            self._html(home_page(self.server.application.csrf_token, str(exc)), status=HTTPStatus.BAD_REQUEST)

    def _read_urlencoded_form(self, max_bytes: int) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            raise IntakeError("request is empty or exceeds the form limit")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise IntakeError("expected a URL-encoded form")
        return {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode("utf-8")).items()}

    def _validate_post(self, fields: dict[str, str]) -> None:
        if not valid_origin(self.headers.get("Origin"), self.headers.get("Host")):
            raise IntakeError("cross-origin requests are not accepted")
        supplied_token = fields.get("csrf_token", "")
        if not supplied_token or not hmac.compare_digest(supplied_token, self.server.application.csrf_token):
            raise IntakeError("invalid CSRF token")

    def log_message(self, format: str, *args: Any) -> None:
        print(f"web: {format % args}")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._bytes(content.encode("utf-8"), "text/html; charset=utf-8", status=status)

    def _bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, attachment: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(payload)


class RobotDoctorHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], application: WebApplication) -> None:
        self.application = application
        try:
            self.address_family = socket.AF_INET6 if ipaddress.ip_address(address[0]).version == 6 else socket.AF_INET
        except ValueError:
            self.address_family = socket.AF_INET
        super().__init__(address, RequestHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Robot Doctor self-service web interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--max-concurrent-tasks", type=int, default=DEFAULT_MAX_CONCURRENT_TASKS)
    parser.add_argument("--max-checkout-size-mb", type=float, default=DEFAULT_MAX_CHECKOUT_BYTES / (1024 * 1024))
    args = parser.parse_args()
    if not is_loopback_host(args.host):
        parser.error("--host must be localhost or a loopback IP address")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.max_concurrent_tasks <= 0:
        parser.error("--max-concurrent-tasks must be positive")
    if args.max_checkout_size_mb <= 0:
        parser.error("--max-checkout-size-mb must be positive")
    application = WebApplication(
        max_concurrent_tasks=args.max_concurrent_tasks,
        max_checkout_bytes=int(args.max_checkout_size_mb * 1024 * 1024),
    )
    server = RobotDoctorHTTPServer((args.host, args.port), application)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Robot Doctor web interface: {url}")
    if not args.no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
