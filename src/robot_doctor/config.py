from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ScanConfig:
    max_file_size_bytes: int = 5 * 1024 * 1024
    max_total_size_bytes: int = 256 * 1024 * 1024
    max_files: int = 100_000
    max_repository_entries: int = 250_000
    dependency_mode: str = "direct"
    suppress_diagnostics: frozenset[str] = field(default_factory=frozenset)
    severity_overrides: dict[str, str] = field(default_factory=dict)
    ignore_dependencies: tuple[str, ...] = ()
    ignore_dependency_pairs: tuple[str, ...] = ()
    minimum_diagnostic_confidence: float = 0.0

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "ScanConfig":
        value = value or {}
        allowed = {
            "max_file_size_bytes",
            "max_total_size_bytes",
            "max_files",
            "max_repository_entries",
            "dependency_mode",
            "suppress_diagnostics",
            "severity_overrides",
            "ignore_dependencies",
            "ignore_dependency_pairs",
            "minimum_diagnostic_confidence",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ConfigError(f"unknown configuration key(s): {', '.join(unknown)}")
        try:
            config = cls(
                max_file_size_bytes=int(value.get("max_file_size_bytes", cls.max_file_size_bytes)),
                max_total_size_bytes=int(value.get("max_total_size_bytes", cls.max_total_size_bytes)),
                max_files=int(value.get("max_files", cls.max_files)),
                max_repository_entries=int(value.get("max_repository_entries", cls.max_repository_entries)),
                dependency_mode=str(value.get("dependency_mode", cls.dependency_mode)),
                suppress_diagnostics=frozenset(str(item) for item in value.get("suppress_diagnostics", [])),
                severity_overrides={str(code): str(severity) for code, severity in value.get("severity_overrides", {}).items()},
                ignore_dependencies=tuple(str(item) for item in value.get("ignore_dependencies", [])),
                ignore_dependency_pairs=tuple(str(item) for item in value.get("ignore_dependency_pairs", [])),
                minimum_diagnostic_confidence=float(value.get("minimum_diagnostic_confidence", 0.0)),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid configuration value: {exc}") from exc
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_file_size_bytes <= 0:
            raise ConfigError("max_file_size_bytes must be positive")
        if self.max_total_size_bytes <= 0:
            raise ConfigError("max_total_size_bytes must be positive")
        if self.max_files <= 0:
            raise ConfigError("max_files must be positive")
        if self.max_repository_entries <= 0:
            raise ConfigError("max_repository_entries must be positive")
        if self.dependency_mode not in {"off", "direct", "all"}:
            raise ConfigError("dependency_mode must be one of: off, direct, all")
        if not 0 <= self.minimum_diagnostic_confidence <= 1:
            raise ConfigError("minimum_diagnostic_confidence must be between 0 and 1")
        invalid_codes = sorted(code for code in self.suppress_diagnostics if not code.startswith("RD") or not code[2:].isdigit())
        if invalid_codes:
            raise ConfigError(f"invalid diagnostic code(s): {', '.join(invalid_codes)}")
        invalid_severities = sorted({severity for severity in self.severity_overrides.values() if severity not in {"error", "warning", "info"}})
        if invalid_severities:
            raise ConfigError(f"invalid diagnostic severity value(s): {', '.join(invalid_severities)}")

    def with_overrides(
        self,
        *,
        max_file_size_bytes: int | None = None,
        max_total_size_bytes: int | None = None,
        max_files: int | None = None,
        max_repository_entries: int | None = None,
        dependency_mode: str | None = None,
        suppress_diagnostics: Iterable[str] = (),
        severity_overrides: dict[str, str] | None = None,
    ) -> "ScanConfig":
        merged_severities = dict(self.severity_overrides)
        merged_severities.update(severity_overrides or {})
        config = replace(
            self,
            max_file_size_bytes=max_file_size_bytes or self.max_file_size_bytes,
            max_total_size_bytes=max_total_size_bytes or self.max_total_size_bytes,
            max_files=max_files or self.max_files,
            max_repository_entries=max_repository_entries or self.max_repository_entries,
            dependency_mode=dependency_mode or self.dependency_mode,
            suppress_diagnostics=self.suppress_diagnostics | frozenset(suppress_diagnostics),
            severity_overrides=merged_severities,
        )
        config.validate()
        return config

    def ignores_dependency(self, package: str, dependency: str) -> bool:
        return any(fnmatch.fnmatch(dependency, pattern) for pattern in self.ignore_dependencies) or any(
            fnmatch.fnmatch(f"{package}:{dependency}", pattern) for pattern in self.ignore_dependency_pairs
        )

    def apply_diagnostic_policy(self, diagnostics: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        kept = []
        suppressed = 0
        for item in diagnostics:
            if item["code"] in self.suppress_diagnostics or item["confidence"] < self.minimum_diagnostic_confidence:
                suppressed += 1
                continue
            if item["code"] in self.severity_overrides:
                item = {**item, "severity": self.severity_overrides[item["code"]]}
            kept.append(item)
        return kept, suppressed

    def to_output(self, suppressed_diagnostics: int = 0) -> dict[str, Any]:
        return {
            "max_file_size_bytes": self.max_file_size_bytes,
            "max_total_size_bytes": self.max_total_size_bytes,
            "max_files": self.max_files,
            "max_repository_entries": self.max_repository_entries,
            "dependency_mode": self.dependency_mode,
            "suppress_diagnostics": sorted(self.suppress_diagnostics),
            "severity_overrides": dict(sorted(self.severity_overrides.items())),
            "ignore_dependencies": list(self.ignore_dependencies),
            "ignore_dependency_pairs": list(self.ignore_dependency_pairs),
            "minimum_diagnostic_confidence": self.minimum_diagnostic_confidence,
            "suppressed_diagnostics": suppressed_diagnostics,
        }


def load_scan_config(path: Path | None = None, repository: Path | None = None) -> ScanConfig:
    selected = path
    if selected is None and repository is not None:
        candidate = repository / ".robot-doctor.json"
        if candidate.is_file():
            selected = candidate
    if selected is None:
        return ScanConfig()
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load configuration {selected}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("configuration root must be a JSON object")
    return ScanConfig.from_mapping(value)
