"""Desktop integration (.desktop entries, icon/menu caches, notifications) and uninstall footprint discovery."""

import glob
import os
import re
import shutil
import subprocess
import time

from .config import APPLICATIONS_DIR, HOME, ICONS_DIR, LOCAL_DIR, PROTECTED_DIRS
from .util import path_size


def _quiet(cmd):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def notify(title, message, icon="application-x-executable"):
    if shutil.which("notify-send"):
        _quiet(["notify-send", title, message, "-i", icon or "application-x-executable"])


def refresh_desktop():
    """Refresh the menu database, KDE's sycoca and the user icon cache."""
    _quiet(["update-desktop-database", APPLICATIONS_DIR])
    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(tool):
            _quiet([tool])
            break
    if shutil.which("gtk-update-icon-cache"):
        _quiet(["gtk-update-icon-cache", "-f", "-q", "-t", ICONS_DIR])


def write_desktop_entry(meta, target_exec, icon_path, desktop_path):
    os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
    exec_args = "%U"
    is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    wm = (meta.get("wm_class") or "").lower()
    if is_wayland and (
        wm.startswith(("cursor", "code", "antigravity", "openchamber"))
        or "electron" in (meta.get("comment") or "").lower()
    ):
        # KWin 6.x supports text-input-v2/v3; text-input-v1 was only in 5.27.
        exec_args = "--enable-wayland-ime --wayland-text-input-version=3 %U"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={meta['app_name']}",
        f"Comment={meta.get('comment') or meta['app_name'] + ' application'}",
        f'Exec="{target_exec}" {exec_args}',
        f"Icon={icon_path or 'application-x-executable'}",
        "Terminal=false",
        f"Categories={meta.get('categories') or 'Utility;'}",
    ]
    if meta.get("wm_class"):
        lines.append(f"StartupWMClass={meta['wm_class']}")
    if meta.get("mime_type"):
        lines.append(f"MimeType={meta['mime_type']}")
    with open(desktop_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(desktop_path, 0o755)


# ==============================================================================
# Footprints (config / cache / data left behind by an app)
# ==============================================================================
def identity_variants(*raw):
    cands = set()
    for r in raw:
        if not r:
            continue
        r = r.strip()
        compact = re.sub(r"[\s\-_]+", "", r)
        cands.update(
            {
                r,
                r.lower(),
                compact,
                compact.lower(),
                re.sub(r"[\s_]+", "-", r).lower().strip("-"),
                re.sub(r"[\s\-]+", "_", r).lower().strip("_"),
            }
        )
    for c in list(cands):
        if len(c) >= 3 and c.lower() not in PROTECTED_DIRS:
            cands.update({f"{c}-updater", f"{c}_updater", f"@{c}-updater"})
    return {c.strip() for c in cands if len(c.strip()) >= 3 and c.strip().lower() not in PROTECTED_DIRS}


def find_footprints(*identities, extra_dirs=()):
    cands = identity_variants(*identities)
    lower = {c.lower() for c in cands}
    roots = [
        ("配置目录 (Config)", os.path.join(HOME, ".config")),
        ("缓存目录 (Cache)", os.path.join(HOME, ".cache")),
        ("应用数据 (Local Share)", os.path.join(HOME, ".local", "share")),
        ("运行状态 (Local State)", os.path.join(HOME, ".local", "state")),
    ]
    forbidden = {HOME, LOCAL_DIR, APPLICATIONS_DIR, ICONS_DIR}
    results, seen = [], set()

    def add(category, p):
        if p in seen or p in forbidden or not os.path.exists(p):
            return
        seen.add(p)
        results.append({"category": category, "path": p, "size": path_size(p), "is_dir": os.path.isdir(p)})

    for category, root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        forbidden.add(root)
        for entry in entries:
            if entry.lower() not in PROTECTED_DIRS and entry.lower() in lower:
                add(category, os.path.join(root, entry))
    for c in cands:
        if c.lower() not in PROTECTED_DIRS:
            add("主目录点文件夹 (Home Dotfile)", os.path.join(HOME, f".{c}"))
    for d in extra_dirs:
        d = os.path.expanduser(d)
        name = os.path.basename(d.rstrip("/")).lower().lstrip(".")
        if name and name not in PROTECTED_DIRS:
            add("插件声明的数据目录", d)
    return results


def find_loose_installers(*names, search_dir=LOCAL_DIR):
    if not os.path.isdir(search_dir):
        return []
    from .registry import sanitize_id

    patterns = set()
    for c in names:
        if c:
            clean = sanitize_id(c)
            if len(clean) >= 3 and clean not in PROTECTED_DIRS:
                patterns.update({clean, c.strip().lower()})
    found = []
    for f in os.listdir(search_dir):
        p = os.path.join(search_dir, f)
        if not os.path.isfile(p) or not re.search(r"\.appimage(\.old)?$", f, re.I):
            continue
        stem = re.sub(r"\.appimage(?:\.old)?$", "", f, flags=re.I).lower()
        if any(stem == pat or stem.startswith(pat + "-") or stem.startswith(pat + "_") for pat in patterns):
            found.append(p)
    return found


def running_pids(exec_path="", exe_prefixes=()):
    """PIDs of this user's processes running the given executable / install prefix."""

    my_pid, my_uid = str(os.getpid()), os.getuid()
    protected = {
        "systemd",
        "cosmic-comp",
        "gnome-shell",
        "kwin",
        "kwin_wayland",
        "plasmashell",
        "sddm",
        "Xwayland",
        "pipewire",
        "wireplumber",
        "dbus-daemon",
        "bash",
        "zsh",
        "fish",
        "python3",
        "python",
    }
    exec_abs = os.path.abspath(exec_path) if exec_path else ""
    needles = [n for n in (exec_abs, *exe_prefixes) if n]
    pids = set()
    for proc_dir in glob.glob("/proc/[0-9]*/"):
        pid = os.path.basename(proc_dir.rstrip("/"))
        if pid == my_pid:
            continue
        try:
            if os.stat(proc_dir).st_uid != my_uid:
                continue
            with open(os.path.join(proc_dir, "comm"), errors="ignore") as f:
                if f.read().strip() in protected:
                    continue
            try:
                exe = os.readlink(os.path.join(proc_dir, "exe"))
            except OSError:
                exe = ""
            with open(os.path.join(proc_dir, "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore")
            if any(n in exe or n in cmd for n in needles):
                pids.add(pid)
        except OSError:
            continue
    return sorted(pids, key=int)


def terminate(pids):
    for pid in pids:
        try:
            os.kill(int(pid), 15)
        except (OSError, ValueError):
            pass
    time.sleep(0.5)
