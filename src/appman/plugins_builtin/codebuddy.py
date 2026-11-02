"""CodeBuddy CN (Tencent) — official .deb repackaged as the pacman package `codebuddy-cn`.

Update feed: the same endpoint the IDE itself polls,
  https://copilot.tencent.com/v2/update?platform=ide-linux-<arch>&version=<installed>
which answers 204 when up to date, or JSON {productVersion, url (.deb), sha256hash} otherwise.
"""

import re
import urllib.parse

from appman.api import HOST_ARCH, AppManError, Update, http_json

NAME = "codebuddy"
DESCRIPTION = "CodeBuddy CN IDE（腾讯官方 deb → pacman 包 codebuddy-cn，腾讯更新接口检测版本）"
APP_NAME = "CodeBuddy CN"
PKGNAME = "codebuddy-cn"

_FEED = "https://copilot.tencent.com/v2/update"
_FILE_RE = re.compile(r"CodeBuddy-linux-(?:x64|arm64)-(\d+(?:\.\d+)+)-[0-9a-f]+-cn(?: \(\d+\))?\.deb$", re.I)


def match(app):
    return app.pacman_pkg == PKGNAME or app.id == PKGNAME


def match_file(path, info):
    return info.get("kind") == "deb" and info.get("fields", {}).get("Package") == "codebuddy-cn"


# ---------------------------------------------------------------- packaging
def deb_pkgname(fields):
    return PKGNAME


def deb_app_name(fields):
    return APP_NAME


def deb_pkgver(fields, filename):
    # control says just "4.12.0"; the build number lives only in the file name and the feed.
    m = _FILE_RE.search(filename)
    return m.group(1) if m else None


def deb_depends(fields, depends):
    # Electron runtime libs; the bundled chrome-sandbox / libvulkan cover the rest.
    return [
        "gtk3",
        "nss",
        "alsa-lib",
        "libxss",
        "libxkbfile",
        "libxtst",
        "libdrm",
        "mesa",
        "libxkbcommon",
        "libsecret",
        "xdg-utils",
    ]


def data_dirs(app):
    return ["~/.codebuddycn", "~/.config/CodeBuddy CN"]


# ---------------------------------------------------------------- version check
def check(app):
    local = app.version
    plat = {"x86_64": "linux-x64", "aarch64": "linux-arm64"}.get(HOST_ARCH)
    if not plat:
        raise AppManError(f"CodeBuddy 没有 {HOST_ARCH} 版本")
    query = urllib.parse.urlencode({"platform": f"ide-{plat}", "version": local if local != "unknown" else "0.0.0"})
    status, data = http_json(f"{_FEED}?{query}")
    if status == 204 or data is None:
        return Update("current", local=local, remote=local, source="CodeBuddy 更新接口")
    if not isinstance(data, dict) or data.get("code"):
        raise AppManError(f"CodeBuddy 更新接口返回错误: {data.get('msg') if isinstance(data, dict) else data}")
    remote = str(data.get("productVersion") or data.get("version") or "").strip()
    url = str(data.get("url") or "")
    if not remote:
        raise AppManError("CodeBuddy 更新接口没有返回版本号")
    if not (url.startswith("https://") and url.lower().endswith(".deb")):
        url = ""
    return Update.compare(
        local,
        remote,
        download_url=url,
        filename=url.rsplit("/", 1)[-1] if url else "",
        checksums={"sha256": data.get("sha256hash") or ""},
        source="CodeBuddy 更新接口",
        kind="deb",
    )
