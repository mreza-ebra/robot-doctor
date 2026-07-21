from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import queue
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.parse import urlparse

from .scanner import ScanCancelled

DEFAULT_MAX_CHECKOUT_BYTES = 1024 * 1024 * 1024
DEFAULT_DNS_TIMEOUT_SECONDS = 10.0
FORBIDDEN_ARCHIVE_COMPONENTS = {".git"}
MAX_CONCURRENT_DNS_LOOKUPS = 4
MINIMUM_PINNED_GIT_VERSION = (2, 37)
_DNS_LOOKUP_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_DNS_LOOKUPS)


class IntakeError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryInput:
    path: Path
    source: str
    source_type: str


def validate_git_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.path.strip("/"):
        raise IntakeError("Git repositories must use a complete https:// URL")
    if parsed.username or parsed.password:
        raise IntakeError("Git URLs containing credentials are not accepted")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise IntakeError("local Git hosts are not accepted by the self-service intake")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise IntakeError("private or local Git addresses are not accepted")
    return value


def git_supports_pinned_https() -> bool:
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
            env={
                **os.environ,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", result.stdout)
    return bool(
        result.returncode == 0
        and match
        and (int(match.group(1)), int(match.group(2))) >= MINIMUM_PINNED_GIT_VERSION
    )


def cancellable_getaddrinfo(
    hostname: str,
    port: int,
    *,
    cancel_check: Callable[[], bool] | None = None,
    timeout_seconds: float = DEFAULT_DNS_TIMEOUT_SECONDS,
) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
    if timeout_seconds <= 0:
        raise IntakeError("Git DNS timeout must be positive")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancel_check and cancel_check():
            raise ScanCancelled("repository DNS resolution cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeError(f"Git host DNS resolution exceeded the {timeout_seconds:g}-second timeout")
        if _DNS_LOOKUP_SLOTS.acquire(timeout=min(0.1, remaining)):
            break

    outcomes: queue.Queue[tuple[list[tuple[int, int, int, str, tuple[object, ...]]] | None, Exception | None]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            outcomes.put((answers, None))
        except Exception as exc:
            outcomes.put((None, exc))
        finally:
            _DNS_LOOKUP_SLOTS.release()

    resolver = threading.Thread(target=resolve, name="robot-doctor-dns", daemon=True)
    try:
        resolver.start()
    except Exception:
        _DNS_LOOKUP_SLOTS.release()
        raise
    while True:
        if cancel_check and cancel_check():
            raise ScanCancelled("repository DNS resolution cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeError(f"Git host DNS resolution exceeded the {timeout_seconds:g}-second timeout")
        try:
            answers, error = outcomes.get(timeout=min(0.1, remaining))
        except queue.Empty:
            continue
        if error is not None:
            raise IntakeError(f"Git host DNS resolution failed for {hostname}: {error}") from error
        return answers or []


def resolve_public_git_host(
    value: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
    timeout_seconds: float = DEFAULT_DNS_TIMEOUT_SECONDS,
) -> tuple[str, int, tuple[str, ...]]:
    parsed = urlparse(validate_git_url(value))
    hostname = parsed.hostname or ""
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise IntakeError(f"Git host DNS resolution failed for {hostname}: {exc}") from exc
    answers = cancellable_getaddrinfo(
        hostname,
        port,
        cancel_check=cancel_check,
        timeout_seconds=timeout_seconds,
    )
    addresses: dict[str, ipaddress.IPv4Address | ipaddress.IPv6Address] = {}
    for answer in answers:
        raw_address = str(answer[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise IntakeError(f"Git host resolved to an invalid address: {raw_address}") from exc
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if not address.is_global or (mapped is not None and not mapped.is_global):
            raise IntakeError(f"Git host resolved to a non-public address: {address}")
        addresses[address.compressed] = address
    if not addresses:
        raise IntakeError(f"Git host DNS resolution returned no addresses for {hostname}")
    ordered = tuple(
        address.compressed
        for address in sorted(addresses.values(), key=lambda item: (item.version, int(item)))
    )
    return hostname, port, ordered


def curlopt_resolve_value(hostname: str, port: int, addresses: tuple[str, ...]) -> str:
    formatted_addresses = ",".join(f"[{value}]" if ":" in value else value for value in addresses)
    return f"{hostname}:{port}:{formatted_addresses}"


def directory_size_exceeds(root: Path, max_bytes: int) -> tuple[bool, int]:
    total_bytes = 0
    directories = [root]
    while directories:
        directory = directories.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                    else:
                        total_bytes += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                if total_bytes > max_bytes:
                    return True, total_bytes
    return False, total_bytes


def repository_content_sha256(root: Path) -> str:
    files: list[Path] = []
    directories = [root]
    while directories:
        directory = directories.pop()
        try:
            entries = os.scandir(directory)
        except OSError as exc:
            raise IntakeError(f"cannot hash extracted repository content: {exc}") from exc
        with entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path)
                except OSError as exc:
                    raise IntakeError(f"cannot hash extracted repository entry {entry.name!r}: {exc}") from exc
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative_path = path.relative_to(root).as_posix().encode("utf-8", errors="surrogateescape")
        try:
            size = path.stat().st_size
            source = path.open("rb")
        except OSError as exc:
            raise IntakeError(f"cannot hash extracted repository file {path}: {exc}") from exc
        digest.update(b"file\0")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        with source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def clone_git_repository(
    url: str,
    destination: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
    timeout_seconds: int = 300,
    dns_timeout_seconds: float = DEFAULT_DNS_TIMEOUT_SECONDS,
    max_checkout_bytes: int = DEFAULT_MAX_CHECKOUT_BYTES,
    access_token: str | None = None,
) -> Path:
    validated_url = validate_git_url(url)
    if not git_supports_pinned_https():
        version = ".".join(str(value) for value in MINIMUM_PINNED_GIT_VERSION)
        raise IntakeError(f"Git {version} or newer is required for DNS-pinned HTTPS intake")
    hostname, port, addresses = resolve_public_git_host(
        validated_url,
        cancel_check=cancel_check,
        timeout_seconds=dns_timeout_seconds,
    )
    if max_checkout_bytes <= 0:
        raise IntakeError("Git checkout size limit must be positive")
    if access_token is not None and (not access_token.strip() or "\n" in access_token or "\r" in access_token):
        raise IntakeError("Git access token must be non-empty and contain no line breaks")
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for variable in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        environment.pop(variable, None)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    configuration = [
        ("http.followRedirects", "false"),
        ("http.curloptResolve", curlopt_resolve_value(hostname, port, addresses)),
        ("http.proxy", ""),
        ("protocol.file.allow", "never"),
        ("protocol.ext.allow", "never"),
    ]
    if access_token:
        hostname = (urlparse(validated_url).hostname or "").lower()
        if hostname in {"github.com", "www.github.com"}:
            encoded = base64.b64encode(f"x-access-token:{access_token}".encode("utf-8")).decode("ascii")
            authorization = f"AUTHORIZATION: basic {encoded}"
        else:
            authorization = f"AUTHORIZATION: Bearer {access_token}"
        configuration.append(("http.extraHeader", authorization))
    environment["GIT_CONFIG_COUNT"] = str(len(configuration))
    for index, (key, config_value) in enumerate(configuration):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = config_value
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_log:
        process = subprocess.Popen(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--single-branch", "--", validated_url, str(destination)],
            stdout=subprocess.DEVNULL,
            stderr=error_log,
            text=True,
            env=environment,
        )
        started = time.monotonic()
        last_size_check = 0.0
        while process.poll() is None:
            if cancel_check and cancel_check():
                stop_process(process)
                raise ScanCancelled("repository clone cancelled")
            now = time.monotonic()
            if now - started > timeout_seconds:
                stop_process(process)
                raise IntakeError(f"Git clone exceeded the {timeout_seconds}-second timeout")
            if now - last_size_check >= 1:
                exceeded, checkout_bytes = directory_size_exceeds(destination, max_checkout_bytes)
                if exceeded:
                    stop_process(process)
                    raise IntakeError(
                        f"Git checkout exceeded the {max_checkout_bytes}-byte limit "
                        f"({checkout_bytes} bytes observed)"
                    )
                last_size_check = now
            time.sleep(0.1)
        if process.returncode:
            error_log.seek(0)
            detail = " ".join(error_log.read().strip().split())[-500:]
            raise IntakeError(f"Git clone failed: {detail or 'unknown Git error'}")
    exceeded, checkout_bytes = directory_size_exceeds(destination, max_checkout_bytes)
    if exceeded:
        raise IntakeError(f"Git checkout exceeded the {max_checkout_bytes}-byte limit ({checkout_bytes} bytes observed)")
    return destination


def extract_zip_upload(
    archive_path: Path,
    destination: Path,
    *,
    max_uncompressed_bytes: int = 500 * 1024 * 1024,
    max_entries: int = 50_000,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise IntakeError(f"uploaded file is not a valid ZIP archive: {exc}") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > max_entries:
            raise IntakeError(f"ZIP archive contains more than {max_entries} entries")
        total_size = sum(item.file_size for item in entries)
        if total_size > max_uncompressed_bytes:
            raise IntakeError(f"ZIP archive expands beyond {max_uncompressed_bytes} bytes")
        destination_resolved = destination.resolve()
        for item in entries:
            archive_path = PurePosixPath(item.filename.replace("\\", "/"))
            if any(part.casefold().rstrip(" .") in FORBIDDEN_ARCHIVE_COMPONENTS for part in archive_path.parts):
                raise IntakeError(f"ZIP archive contains forbidden Git metadata: {item.filename}")
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise IntakeError(f"ZIP archive contains a symbolic link: {item.filename}")
            target = (destination / Path(*archive_path.parts)).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise IntakeError(f"ZIP archive contains an unsafe path: {item.filename}") from exc
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    children = [path for path in destination.iterdir() if path.name != "__MACOSX"]
    return children[0] if len(children) == 1 and children[0].is_dir() else destination


@contextmanager
def materialize_repository(
    source: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
    max_checkout_bytes: int | None = None,
    access_token: str | None = None,
) -> Iterator[RepositoryInput]:
    local = Path(source).expanduser()
    if local.exists():
        if not local.is_dir():
            raise IntakeError(f"repository path is not a directory: {local}")
        yield RepositoryInput(local.resolve(), str(local.resolve()), "local")
        return
    if not source.startswith("https://"):
        raise IntakeError(f"repository path does not exist and is not an HTTPS Git URL: {source}")
    with tempfile.TemporaryDirectory(prefix="robot-doctor-git-") as directory:
        checkout = clone_git_repository(
            source,
            Path(directory) / "repository",
            cancel_check=cancel_check,
            max_checkout_bytes=DEFAULT_MAX_CHECKOUT_BYTES if max_checkout_bytes is None else max_checkout_bytes,
            access_token=access_token,
        )
        yield RepositoryInput(checkout, source, "git")
