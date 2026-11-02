"""Paths, constants and host facts shared by every appship module.

New installs use the XDG base directories. Existing installs with
``~/local/.apps.json`` automatically stay on the legacy layout until
``appship migrate --apply`` moves them.
"""

import os
import platform
import re

HOME = os.path.expanduser("~")


def _xdg(env_name, default):
    value = os.environ.get(env_name)
    return os.path.abspath(os.path.expanduser(value)) if value else os.path.join(HOME, default)


XDG_DATA_HOME = _xdg("XDG_DATA_HOME", ".local/share")
XDG_CONFIG_HOME = _xdg("XDG_CONFIG_HOME", ".config")
XDG_STATE_HOME = _xdg("XDG_STATE_HOME", ".local/state")
XDG_CACHE_HOME = _xdg("XDG_CACHE_HOME", ".cache")
XDG_BIN_HOME = os.path.join(HOME, ".local", "bin")

APPSHIP_DATA_DIR = os.path.join(XDG_DATA_HOME, "appship")
APPSHIP_CONFIG_DIR = os.path.join(XDG_CONFIG_HOME, "appship")
APPSHIP_STATE_DIR = os.path.join(XDG_STATE_HOME, "appship")
APPSHIP_CACHE_DIR = os.path.join(XDG_CACHE_HOME, "appship")

# Target paths are always the XDG layout; migrate uses them regardless of the
# currently active legacy mode.
TARGET_APPS_DIR = os.path.join(APPSHIP_DATA_DIR, "apps")
TARGET_REGISTRY_PATH = os.path.join(APPSHIP_DATA_DIR, "apps.json")
TARGET_PLUGINS_DIR = os.path.join(APPSHIP_DATA_DIR, "plugins")
TARGET_DEB_DIR = os.path.join(APPSHIP_DATA_DIR, "debpkgs")

APPLICATIONS_DIR = os.path.join(XDG_DATA_HOME, "applications")
ICONS_DIR = os.path.join(XDG_DATA_HOME, "icons")
DOWNLOADS_DIR = os.path.join(HOME, "Downloads")
CACHE_DIR = APPSHIP_CACHE_DIR
DOWNLOAD_CACHE = os.path.join(CACHE_DIR, "downloads")

LEGACY_LOCAL_DIR = os.path.join(HOME, "local")
LEGACY_DB_PATH = os.path.join(LEGACY_LOCAL_DIR, ".apps.json")
LEGACY_PLUGINS_DIR = os.path.join(LEGACY_LOCAL_DIR, "appman.d", "plugins")
LEGACY_DEB_DIR = os.path.join(LEGACY_LOCAL_DIR, "debpkgs")

_legacy_env = os.environ.get("APPSHIP_LEGACY")
if _legacy_env is None:
    USE_LEGACY_LAYOUT = os.path.exists(LEGACY_DB_PATH) and not os.path.exists(TARGET_REGISTRY_PATH)
else:
    USE_LEGACY_LAYOUT = _legacy_env.strip().lower() in ("1", "true", "yes", "on")

if USE_LEGACY_LAYOUT:
    LOCAL_DIR = LEGACY_LOCAL_DIR
    VERSIONS_DB_PATH = LEGACY_DB_PATH
    PLUGINS_DIR = LEGACY_PLUGINS_DIR
    DEB_DIR = LEGACY_DEB_DIR
else:
    LOCAL_DIR = TARGET_APPS_DIR
    VERSIONS_DB_PATH = TARGET_REGISTRY_PATH
    PLUGINS_DIR = TARGET_PLUGINS_DIR
    DEB_DIR = TARGET_DEB_DIR

# Per-path overrides for tests and portable/custom installs.
LOCAL_DIR = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_APPS_DIR", LOCAL_DIR)))
VERSIONS_DB_PATH = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_REGISTRY", VERSIONS_DB_PATH)))
PLUGINS_DIR = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_PLUGINS_DIR", PLUGINS_DIR)))
DEB_DIR = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_DEB_DIR", DEB_DIR)))
CACHE_DIR = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_CACHE_DIR", CACHE_DIR)))
DOWNLOAD_CACHE = os.path.join(CACHE_DIR, "downloads")
APPLICATIONS_DIR = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_APPLICATIONS_DIR", APPLICATIONS_DIR)))
ICONS_DIR = os.path.abspath(os.path.expanduser(os.environ.get("APPSHIP_ICONS_DIR", ICONS_DIR)))

CONFIG_DIR = APPSHIP_CONFIG_DIR
STATE_DIR = APPSHIP_STATE_DIR
APPSHIP_HOME = APPSHIP_DATA_DIR  # compatibility alias for older callers
APPS_DIR = LOCAL_DIR

# Entries in the app install root that belong to appship itself and must never
# be treated as an app.
RESERVED_LOCAL_NAMES = {"appship", "appship.d", "appman", "appman.d", "debpkgs", "data", ".apps.json", "apps.json"}

# ------------------------------------------------------------------ host architecture
_MACHINE = platform.machine().lower()
HOST_ARCH = {"amd64": "x86_64", "arm64": "aarch64", "armv7l": "armv7h"}.get(_MACHINE, _MACHINE)

ARCH_ALIASES = {
    "x86_64": ("x86_64", "x86-64", "amd64", "x64"),
    "aarch64": ("aarch64", "arm64"),
    "armv7h": ("armv7l", "armv7h", "armhf", "armv7"),
    "i686": ("i686", "i386", "ia32"),
}


def _alias_re(names):
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in names) + r")(?![a-z0-9])")


HOST_ARCH_RE = _alias_re(ARCH_ALIASES.get(HOST_ARCH, (HOST_ARCH,)))
FOREIGN_ARCH_RE = _alias_re([a for arch, names in ARCH_ALIASES.items() if arch != HOST_ARCH for a in names])

# ------------------------------------------------------------------ cleanup safety
# Directories that must NEVER be deleted during footprint cleanup.
PROTECTED_DIRS = {
    # System & Desktop core
    "applications",
    "icons",
    "desktop-directories",
    "mime",
    "themes",
    "fonts",
    "trash",
    "sounds",
    "flatpak",
    "systemd",
    "keyrings",
    "gnome-shell",
    "nautilus",
    "cosmic",
    "cosmic-comp",
    "cosmic-store",
    "pop",
    "pop-launcher",
    "dconf",
    "pulse",
    "pipewire",
    "wireplumber",
    "gconf",
    "gtk-3.0",
    "gtk-4.0",
    "glib-2.0",
    "xsettingsd",
    "fontconfig",
    "autostart",
    "environment.d",
    "ibus",
    "ibus-table",
    "fcitx",
    "fcitx5",
    "color-schemes",
    "gvfs-metadata",
    "logs",
    # KDE / Plasma desktop core
    "plasma",
    "plasmashell",
    "plasma-workspace",
    "kded6",
    "kded5",
    "kwin",
    "kwinrc",
    "kde",
    "dolphin",
    "konsole",
    "kate",
    "kio",
    "sddm",
    "krunner",
    # Shell & Developer toolchains
    "fish",
    "bash",
    "zsh",
    "git",
    "pip",
    "cargo",
    "rustup",
    "npm",
    "yarn",
    "pnpm",
    "fnm",
    "go",
    "go-build",
    "gopls",
    "containers",
    # Editors & Essential Work tools
    "alacritty",
    "kitty",
    "tmux",
    "code",
    "vscode",
    "cursor",
    "antigravity",
    "antigravity ide",
    "jetbrains",
    "nvim",
    "vim",
    "nano",
    # Browsers
    "google-chrome",
    "chromium",
    "firefox",
    "bravesoftware",
    # System base paths
    "local",
    "share",
    "config",
    "cache",
    "state",
    "bin",
    "doc",
    "man",
    "tmp",
    # appship itself (and the legacy appman names)
    "appship",
    "appship.d",
    "appman",
    "appman.d",
    "debpkgs",
}
