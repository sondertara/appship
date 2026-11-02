"""AppImage inspection: listing/reading members with 7z, metadata, icons and the embedded update info."""

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from collections import Counter

from .util import clean_version

_METAINFO_PATTERNS = ("*.desktop", "*.png", "*.svg", "*.metainfo.xml", "*.appdata.xml", "package.json")


def have_7z():
    return bool(shutil.which("7z"))


def exec_signature(path):
    """'<size>:<mtime>' of an executable file, or '' when missing."""
    try:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


# ==============================================================================
# Archive access
# ==============================================================================
def list_members(path):
    """[(name, is_dir, is_symlink)] using 7z's technical listing (robust against spaces in names)."""
    proc = subprocess.run(
        ["7z", "l", "-slt", "-ba", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace"
    )
    if proc.returncode not in (0, 1):  # 1 = warnings (e.g. "data after end of archive")
        return []
    members, cur = [], {}

    def flush():
        if cur.get("Path"):
            mode = cur.get("Mode", "")
            members.append((cur["Path"], cur.get("Folder") == "+" or mode.startswith("d"), mode.startswith("l")))

    for line in proc.stdout.splitlines():
        if not line.strip():
            flush()
            cur = {}
            continue
        if " = " in line:
            k, v = line.split(" = ", 1)
            cur[k.strip()] = v
    flush()
    return members


def read_member(path, member, limit=None):
    """Bytes of one archive member ('' when absent). `limit` caps how much is read."""
    proc = subprocess.Popen(["7z", "e", "-so", path, member], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        data = proc.stdout.read(limit) if limit else proc.stdout.read()
    finally:
        proc.kill()
        proc.wait()
    return data or b""


# ==============================================================================
# Embedded update information (.upd_info)
# ==============================================================================
_UPDATE_PREFIXES = ("zsync|", "gh-releases-zsync|", "pling-v1-zsync|", "bintray-zsync|", "gh-releases-direct|")


def read_update_info(path):
    """The embedded AppImage update-information string, or ''."""
    try:
        with open(path, "rb") as handle:
            handle.seek(8)
            if handle.read(3) == b"AI\x01":
                handle.seek(0x8373)
                blob = handle.read(512)
            else:
                blob = _read_elf_section(handle, ".upd_info")
    except OSError:
        return ""
    if not blob:
        return ""
    text = blob.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
    return text if text.startswith(_UPDATE_PREFIXES) else ""


def _read_elf_section(handle, section_name):
    handle.seek(0)
    ident = handle.read(16)
    if len(ident) < 16 or ident[:4] != b"\x7fELF":
        return b""
    endian = "<" if ident[5] == 1 else ">"
    if ident[4] == 2:
        header = handle.read(48)
        if len(header) < 48:
            return b""
        vals = struct.unpack(endian + "HHIQQQIHHHHHH", header)
        e_shoff, e_shentsize, e_shnum, e_shstrndx = vals[5], vals[10], vals[11], vals[12]
        shfmt, shsize = endian + "IIQQQQIIQQ", 64
    elif ident[4] == 1:
        header = handle.read(36)
        if len(header) < 36:
            return b""
        vals = struct.unpack(endian + "HHIIIIIHHHHHH", header)
        e_shoff, e_shentsize, e_shnum, e_shstrndx = vals[5], vals[9], vals[10], vals[11]
        shfmt, shsize = endian + "IIIIIIIIII", 40
    else:
        return b""
    if not e_shoff or not e_shnum or e_shstrndx >= e_shnum or e_shentsize < shsize:
        return b""

    def read_sh(index):
        handle.seek(e_shoff + index * e_shentsize)
        raw = handle.read(shsize)
        return struct.unpack(shfmt, raw) if len(raw) == shsize else None

    strhdr = read_sh(e_shstrndx)
    if not strhdr:
        return b""
    handle.seek(strhdr[4])
    strtab = handle.read(strhdr[5])
    for index in range(e_shnum):
        sec = read_sh(index)
        if not sec:
            continue
        end = strtab.find(b"\x00", sec[0])
        if end >= 0 and strtab[sec[0] : end].decode("ascii", "replace") == section_name:
            handle.seek(sec[4])
            return handle.read(min(sec[5], 8192))
    return b""


# ==============================================================================
# Metadata extraction
# ==============================================================================
def parse_desktop_entry(text):
    """Key/value pairs of the [Desktop Entry] group (unlocalised keys only)."""
    out, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section == "Desktop Entry" and "=" in line:
            k, v = line.split("=", 1)
            if "[" not in k:
                out.setdefault(k.strip(), v.strip())
    return out


def extract_metadata(app_path):
    """Name, version, desktop fields and icon of an AppImage (version falls back through 5 sources)."""
    filename = os.path.basename(app_path)
    stem = re.sub(r"\.appimage$", "", filename, flags=re.I)
    m = re.match(r"^(.*?)[-_]v?(\d+\.\d+.*)$", stem)
    meta = {
        "filename": filename,
        "app_name": m.group(1) if m else clean_version(stem),
        "version": clean_version(m.group(2)) if m else "unknown",
        "comment": "",
        "categories": "Utility;",
        "wm_class": "",
        "mime_type": "",
        "icon_ext": "png",
        "icon_data": None,
    }
    try:
        if (os.stat(app_path).st_mode & 0o111) != 0o111:
            os.chmod(app_path, 0o755)
    except OSError:
        pass
    exec_cmd = None

    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            ["7z", "x", app_path, "-r", *_METAINFO_PATTERNS, f"-o{tmpdir}", "-y"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        files = []
        for root, _dirs, names in os.walk(tmpdir):
            for n in names:
                p = os.path.join(root, n)
                if os.path.isfile(p) and not os.path.islink(p):
                    files.append(p)

        # 1. desktop entry
        desktops = [p for p in files if p.endswith(".desktop")]
        desktops.sort(key=lambda p: "usr/share/applications" not in p)
        if desktops:
            with open(desktops[0], "r", encoding="utf-8", errors="ignore") as fh:
                entry = parse_desktop_entry(fh.read())
            meta["app_name"] = entry.get("Name") or meta["app_name"]
            meta["comment"] = entry.get("Comment", "")
            meta["categories"] = entry.get("Categories") or meta["categories"]
            meta["wm_class"] = entry.get("StartupWMClass", "")
            meta["mime_type"] = entry.get("MimeType", "")
            if entry.get("Exec"):
                exec_cmd = os.path.basename(entry["Exec"].split()[0])
            if meta["version"] == "unknown":
                for key in ("X-AppImage-Version", "X-AppStream-Version"):
                    if entry.get(key):
                        meta["version"] = clean_version(entry[key])
                        break
            if meta["version"] == "unknown":
                ver = entry.get("Version", "")
                # "Version=" is the desktop-spec version, only trust it when it doesn't look like one.
                if re.match(r"^\d+\.\d+", ver) and ver not in ("1.0", "1.1", "1.2", "1.3", "1.4", "1.5"):
                    meta["version"] = clean_version(ver)

        # 2. AppStream
        if meta["version"] == "unknown":
            for p in files:
                if p.endswith((".metainfo.xml", ".appdata.xml")):
                    with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                    mv = re.search(r'<release\s+[^>]*version=["\']([^"\']+)["\']', content) or re.search(
                        r"<version>([^<]+)</version>", content
                    )
                    if mv:
                        meta["version"] = clean_version(mv.group(1))
                        break

        # 3. package.json (Electron / Node apps)
        if meta["version"] == "unknown":
            for p in files:
                if os.path.basename(p) == "package.json" and "node_modules" not in p:
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                            v = str(json.load(fh).get("version") or "").strip()
                    except (ValueError, AttributeError):
                        continue
                    if v and v not in ("0.0.0", "1.0.0"):
                        meta["version"] = clean_version(v)
                        break

        # 4. binary strings of the main executable
        if meta["version"] == "unknown":
            v = _version_from_binary(app_path, meta["app_name"], exec_cmd)
            if v:
                meta["version"] = clean_version(v)

        # 5. icon: prefer the root .DirIcon-level icon, then SVG, then bigger PNG
        icons = []
        for p in files:
            if p.endswith((".png", ".svg")) and os.path.getsize(p) > 200:
                is_svg = p.endswith(".svg")
                is_root = os.path.dirname(p) == tmpdir
                icons.append(
                    (p, (1000 if is_root else 0) + (500 if is_svg else 0) + min(os.path.getsize(p), 500), is_svg)
                )
        if icons:
            chosen = max(icons, key=lambda t: t[1])
            meta["icon_ext"] = "svg" if chosen[2] else "png"
            with open(chosen[0], "rb") as fh:
                meta["icon_data"] = fh.read()
    return meta


def _version_from_binary(app_path, app_name, exec_name=None):
    members = [n for n, is_dir, is_link in list_members(app_path) if not is_dir and not is_link]
    if not members:
        return None
    clean = re.sub(r"[^a-zA-Z0-9]", "", app_name).lower()
    variants = {v for v in (app_name, clean, exec_name, re.sub(r"[^a-zA-Z0-9]", "", exec_name or "").lower()) if v}
    targets = [f"usr/bin/{v}".lower() for v in variants]
    cands = [f for f in members if f.lower() in targets]
    cands += [f for f in members if f.startswith("usr/bin/") and not f.endswith(".sh") and f not in cands]
    cands += [f for f in ("AppRun.wrapped", "AppRun") if f in members]
    pattern = re.compile(
        rb"(?i)(?:" + "|".join(re.escape(v) for v in variants).encode() + rb")[\s/\-_:]+v?"
        rb"(\d{1,4}\.\d{1,4}(?:\.\d{1,4})?(?:-(?:alpha|beta|rc|dev|preview)[.\d]*)?)"
    )
    for cand in cands:
        data = read_member(app_path, cand, limit=35 * 1024 * 1024)
        if not data.startswith(b"\x7fELF"):
            continue
        found = pattern.findall(data)
        if found:
            return Counter(x.decode(errors="ignore") for x in found).most_common(1)[0][0]
    return None
