"""Stable surface for plugins: import from here, not from internal modules."""

from .checks import Update, electron_generic, github_release, zsync_source
from .config import DEB_DIR, HOME, HOST_ARCH, LOCAL_DIR
from .registry import App, pacman_to_upstream_version
from .util import (
    AppShipError,
    clean_version,
    compare_versions,
    http_head,
    http_json,
    http_request,
    version_from_filename,
    version_from_tag,
)

# Legacy plugins imported AppManError; keep the alias for the migration cycle.
AppManError = AppShipError

__all__ = [
    "App",
    "AppManError",
    "AppShipError",
    "DEB_DIR",
    "HOME",
    "HOST_ARCH",
    "LOCAL_DIR",
    "Update",
    "clean_version",
    "compare_versions",
    "electron_generic",
    "github_release",
    "http_head",
    "http_json",
    "http_request",
    "pacman_to_upstream_version",
    "version_from_filename",
    "version_from_tag",
    "zsync_source",
]
