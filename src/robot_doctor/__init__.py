"""Robot Doctor static analysis package."""

from .config import ScanConfig
from .scanner import SCANNER_VERSION, SCHEMA_VERSION, scan_repository

__all__ = ["SCANNER_VERSION", "SCHEMA_VERSION", "ScanConfig", "scan_repository"]
__version__ = SCANNER_VERSION
