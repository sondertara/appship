"""Stable surface for plugins: import from here, not from internal modules."""

from .checks import Update, electron_generic, github_release, zsync_source
from .config import DEB_DIR, HOME, HOST_ARCH, LOCAL_DIR
from .plugin_api import API_VERSION
from .registry import App
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
    "API_VERSION",
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
    "version_from_filename",
    "version_from_tag",
    "zsync_source",
]
