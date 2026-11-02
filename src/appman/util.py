"""Small helpers: terminal formatting, prompts, version handling, HTTP and downloads."""

import base64
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

try:
    from packaging.version import InvalidVersion
    from packaging.version import parse as _pkg_parse
except ImportError:  # pragma: no cover - packaging is optional
    _pkg_parse = None
    InvalidVersion = Exception

from .config import FOREIGN_ARCH_RE, HOST_ARCH_RE


class AppManError(RuntimeError):
    """Expected failure with a user-facing message."""


# ==============================================================================
# Terminal output
# ==============================================================================
def display_width(s):
    """Terminal display width of a string, counting CJK characters as two columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in str(s))


def pad(s, width):
    s = str(s)
    return s + " " * max(0, width - display_width(s))


def format_size(size_bytes):
    if not size_bytes or size_bytes <= 0:
        return "0 B"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0


def path_size(path):
    """Total size in bytes of a file or directory (symlinks are not followed)."""
    if not os.path.exists(path) or os.path.islink(path):
        return 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def confirm(msg, default=True, auto_yes=False):
    """Interactive yes/no prompt; non-interactive shells get the default."""
    if auto_yes:
        return True
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"{msg} {suffix}: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    if not ans:
        return default
    return ans in ("y", "yes")


# ==============================================================================
# Versions
# ==============================================================================
_ARCH_SUFFIX_RE = re.compile(
    r"[-_.](?:(?:linux|gnu|glibc|musl)[-_])?"
    r"(?:x86_64|x86-64|amd64|x64|arm64|aarch64|armv7l|armhf|i386|i686)"
    r"(?:[-_](?:linux|gnu|glibc|musl))?$",
    re.I,
)


def clean_version(raw):
    """Normalise a version string: strip a leading 'v', arch/OS suffixes and stray separators."""
    if not raw:
        return "unknown"
    v = str(raw).strip()
    v = re.sub(r"^(?:version|ver)[\s:=]*", "", v, flags=re.I)
    if len(v) > 1 and v[0] in "vV" and v[1].isdigit():
        v = v[1:]
    v = _ARCH_SUFFIX_RE.sub("", v)
    v = re.sub(r"[-_.](?:linux|appimage)$", "", v, flags=re.I)
    v = v.strip("-_. ")
    return v or "unknown"


def version_from_tag(tag):
    """Pull a version out of a git tag such as 'v1.2.3', 'app-v1.2.3' or 'v2026-10-13'."""
    if not tag:
        return "unknown"
    m = re.search(r"(\d+(?:[.\-]\d+)+(?:[-.+]?[A-Za-z][\w.]*)?)", str(tag))
    if not m:
        return "unknown"
    v = m.group(1)
    # Date tags (2026-10-13) are dotted versions written with dashes.
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", v):
        v = ".".join(str(int(p)) for p in v.split("-"))
    return clean_version(v)


def version_from_filename(filename):
    """Pull a version out of a release file name or URL."""
    if not filename:
        return "unknown"
    base = os.path.basename(str(filename).split("?", 1)[0].split("#", 1)[0])
    base = re.sub(r"(?i)\.(zsync|appimage|deb|rpm|tar\.gz|zip)$", "", base)
    base = re.sub(r"(?i)\.(zsync|appimage)$", "", base)
    m = re.match(r"^(.*?)[-_]v?(\d+\.\d+.*)$", base)
    if not m:
        return "unknown"
    return clean_version(m.group(2))


def _canonical_date_version(v):
    """'2026.09.24' and '2026.9.24' should compare equal."""
    if re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", v):
        return ".".join(str(int(p)) for p in v.split("."))
    return v


def compare_versions(v1, v2):
    """Return 1 / 0 / -1. 'unknown' always sorts lowest."""
    v1 = _canonical_date_version(clean_version(v1)) if v1 else "unknown"
    v2 = _canonical_date_version(clean_version(v2)) if v2 else "unknown"
    if v1 == v2:
        return 0
    if v1 == "unknown":
        return -1
    if v2 == "unknown":
        return 1
    if _pkg_parse is not None:
        try:
            p1, p2 = _pkg_parse(v1), _pkg_parse(v2)
            return (p1 > p2) - (p1 < p2)
        except (InvalidVersion, TypeError):
            pass

    def tokens(v):
        return [
            (0, int(t)) if t.isdigit() else (1, t.lower())
            for t in re.split(r"(\d+)", v)
            if t not in ("", ".", "-", "_")
        ]

    t1, t2 = tokens(v1), tokens(v2)
    return (t1 > t2) - (t1 < t2)


def is_prerelease_version(v):
    return bool(re.search(r"(?i)(alpha|beta|rc|dev|nightly|preview|canary|insider)", v or ""))


# ==============================================================================
# Release asset selection
# ==============================================================================
def asset_matches_host(name, ext):
    """True when a release asset is a `ext` file usable on this host's architecture."""
    low = name.lower()
    if not low.endswith(ext.lower()):
        return False
    if HOST_ARCH_RE.search(low):
        return True
    return not FOREIGN_ARCH_RE.search(low)


def pick_asset(names, ext, hint=""):
    """Pick the best asset name for this host. `hint` is e.g. the currently installed file name."""
    cands = [n for n in names if asset_matches_host(n, ext)]
    if not cands:
        return None

    def score(n):
        low = n.lower()
        s = 0
        if HOST_ARCH_RE.search(low):
            s += 10
        if hint:
            stem = re.split(r"[-_]v?\d", hint.lower(), 1)[0]
            if stem and low.startswith(stem):
                s += 5
        return s

    return max(cands, key=score)


# ==============================================================================
# HTTP
# ==============================================================================
def _github_token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def http_request(url, headers=None, timeout=20, limit=None, method="GET"):
    """HTTPS request. Returns (status, body_bytes, response_headers). 204/304/404 do not raise."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise AppManError(f"不是 https 地址: {url}")
    hdrs = {"User-Agent": "appman"}
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "api.github.com" and _github_token():
        hdrs["Authorization"] = f"Bearer {_github_token()}"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not resp.geturl().startswith("https://"):
                raise AppManError("请求被重定向到非 https 地址")
            body = b"" if method == "HEAD" else (resp.read(limit) if limit else resp.read())
            return getattr(resp, "status", 200), body, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        if exc.code in (204, 304, 404):
            return exc.code, b"", dict(exc.headers.items()) if exc.headers else {}
        if exc.code == 403 and host == "api.github.com":
            raise AppManError("GitHub API 被限流（可设置 GITHUB_TOKEN 环境变量）") from exc
        raise AppManError(f"HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise AppManError(f"网络请求失败: {exc.reason}") from exc
    except TimeoutError as exc:
        raise AppManError("网络请求超时") from exc


def http_json(url, headers=None, timeout=20):
    """GET and decode JSON. Returns (status, data) where data is None for 204/404."""
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    status, body, _ = http_request(url, headers=hdrs, timeout=timeout)
    if status in (204, 404) or not body.strip():
        return status, None
    try:
        return status, json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppManError(f"返回的不是 JSON: {url}") from exc


def http_head(url, timeout=20):
    """HEAD request; returns lower-cased response headers (empty dict on 404)."""
    status, _, headers = http_request(url, timeout=timeout, method="HEAD")
    if status == 404:
        return {}
    return {k.lower(): v for k, v in headers.items()}


# ==============================================================================
# Downloads & checksums
# ==============================================================================
def normalise_digest(algo, value):
    """Accept hex or base64 digests (electron-builder uses base64 sha512); return lower hex."""
    value = (value or "").strip()
    if ":" in value and value.split(":", 1)[0].lower() in ("sha256", "sha512", "md5"):
        value = value.split(":", 1)[1]
    if not value:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]+", value):
        return value.lower()
    try:
        return base64.b64decode(value, validate=True).hex()
    except ValueError:
        return ""


def file_digest(path, algo="sha256"):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest_dir, filename=None, checksums=None):
    """Download `url` into dest_dir via a .part file, verifying any {'sha256'|'sha512'|'md5': value}."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise AppManError("下载地址不是 https")
    name = filename or os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)) or "download"
    name = re.sub(r"[^\w.+\-]", "_", name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    part = dest + ".part"

    wanted = {a: normalise_digest(a, v) for a, v in (checksums or {}).items() if v}
    wanted = {a: v for a, v in wanted.items() if v}
    hashers = {a: hashlib.new(a) for a in wanted}

    req = urllib.request.Request(url, headers={"User-Agent": "appman"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            if not resp.geturl().startswith("https://"):
                raise AppManError("下载被重定向到非 https 地址")
            total = int(resp.headers.get("Content-Length") or 0)
            done, last_pct = 0, -1
            tty = sys.stdout.isatty()
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                for h in hashers.values():
                    h.update(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else -1
                if tty and pct != last_pct:
                    tail = f" / {format_size(total)}" if total else ""
                    head = f"{pct:3d}% " if total else ""
                    print(f"\r   ⬇ {head}{format_size(done)}{tail}   ", end="", flush=True)
                    last_pct = pct
            if tty:
                print()
        for algo, expect in wanted.items():
            got = hashers[algo].hexdigest()
            if got != expect:
                raise AppManError(f"{algo} 校验失败 (期望 {expect[:16]}…, 实际 {got[:16]}…)")
    except urllib.error.URLError as exc:
        _remove_quietly(part)
        raise AppManError(f"下载失败: {getattr(exc, 'reason', exc)}") from exc
    except BaseException:
        _remove_quietly(part)
        raise
    os.replace(part, dest)
    if wanted:
        print(f"   🔐 校验通过: {', '.join(sorted(wanted))}")
    return dest


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass
