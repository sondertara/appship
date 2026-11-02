"""Upstream version checks.

Resolution order for every app:
  1. a plugin whose match(app) is true and whose check() returns a result
  2. the cached / pinned update source in the registry entry
  3. discovery (AppImage only), first hit wins:
       a. embedded update info (.upd_info: zsync / gh-releases-zsync)
       b. electron-builder resources/app-update.yml (github or generic provider)
       c. GitHub URLs in desktop / AppStream files
       d. GitHub URLs inside the main executable (Tauri / Rust / Go apps)
The discovered source is cached with the executable's size+mtime and re-discovered after it changes.
"""

import fnmatch
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import appimage
from .config import PLUGINS_DIR
from .util import (
    AppShipError,
    clean_version,
    compare_versions,
    http_json,
    http_request,
    is_prerelease_version,
    pad,
    pick_asset,
    version_from_filename,
    version_from_tag,
)

STATUS_LABEL = {
    "update": "有新版本",
    "current": "已是最新",
    "ahead": "本地较新",
    "none": "无更新来源",
    "error": "检查失败",
}


class Update:
    """Result of an upstream check."""

    def __init__(
        self,
        status,
        local="unknown",
        remote="",
        download_url="",
        filename="",
        checksums=None,
        source="",
        detail="",
        force=False,
        kind="",
    ):
        self.status = status
        self.local = local or "unknown"
        self.remote = remote
        self.download_url = download_url
        self.filename = filename
        self.checksums = {k: v for k, v in (checksums or {}).items() if v}
        self.source = source
        self.detail = detail
        self.force = force  # install even when the version string is unchanged (e.g. same-version rebuilds)
        self.kind = kind  # 'appimage' | 'deb' (what download_url points at)

    @classmethod
    def compare(cls, local, remote, **kw):
        """Build a result by comparing versions."""
        cmp_res = compare_versions(remote, local)
        status = "update" if cmp_res > 0 else ("ahead" if cmp_res < 0 else "current")
        return cls(status, local=local, remote=remote, **kw)

    @classmethod
    def error(cls, detail, local="unknown", source=""):
        return cls("error", local=local, detail=detail, source=source)

    @classmethod
    def none(cls, detail="", local="unknown"):
        return cls("none", local=local, detail=detail)

    def __repr__(self):
        return f"Update({self.status}, {self.local!r} -> {self.remote!r}, {self.source!r})"


# ==============================================================================
# Source resolvers
# ==============================================================================
def github_release(repo, local, ext=".AppImage", hint="", allow_prerelease=None, asset_glob="", kind="appimage"):
    """Newest release of `repo` that ships an `ext` asset usable on this host."""
    status, releases = http_json(
        f"https://api.github.com/repos/{repo}/releases?per_page=40", headers={"Accept": "application/vnd.github+json"}
    )
    if status == 404 or not isinstance(releases, list):
        raise AppShipError(f"GitHub 上找不到仓库 {repo} 的 Release")
    if allow_prerelease is None:
        allow_prerelease = is_prerelease_version(local)

    def candidates(include_pre):
        out = []
        for rel in releases:
            if rel.get("draft") or (rel.get("prerelease") and not include_pre):
                continue
            assets = {a.get("name") or "": a for a in rel.get("assets") or []}
            names = [n for n in assets if not asset_glob or fnmatch.fnmatch(n, asset_glob)]
            name = pick_asset(names, ext, hint=hint)
            if not name:
                continue
            version = version_from_tag(rel.get("tag_name"))
            if version == "unknown":
                version = version_from_filename(name)
            out.append((version, rel, assets[name], assets))
        return out

    found = candidates(allow_prerelease) or candidates(True)
    if not found:
        raise AppShipError(f"{repo} 的 Release 中没有适用于本机的 {ext} 文件")
    best = found[0]
    for item in found[1:]:
        if compare_versions(item[0], best[0]) > 0:
            best = item
    version, rel, asset, assets = best
    checksums = {}
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        checksums["sha256"] = digest.split(":", 1)[1]
    elif not digest:
        checksums.update(_electron_yml_checksum(assets, asset.get("name") or ""))
    url = asset.get("browser_download_url") or ""
    return Update.compare(
        local,
        version,
        download_url=url if url.startswith("https://") else "",
        filename=asset.get("name") or "",
        checksums=checksums,
        source=f"GitHub Releases ({repo})",
        kind=kind,
    )


def _electron_yml_checksum(assets, filename):
    """sha512 for `filename` from an electron-builder latest-linux*.yml asset, if present."""
    for name, a in assets.items():
        if re.fullmatch(r"(latest|beta|alpha)-linux.*\.yml", name) and a.get("browser_download_url", "").startswith(
            "https://"
        ):
            try:
                _, body, _ = http_request(a["browser_download_url"], limit=65536)
            except AppShipError:
                continue
            files = _parse_builder_yml(body.decode("utf-8", "replace"))
            if filename in files.get("files", {}):
                return {"sha512": files["files"][filename]}
    return {}


def _parse_builder_yml(text):
    """Minimal parser for electron-builder latest*.yml / app-update.yml (flat keys + files list)."""
    out, files, cur = {}, {}, None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*-\s*url:\s*(.+)$", line)
        if m:
            cur = m.group(1).strip().strip("'\"")
            files.setdefault(cur, "")
            continue
        m = re.match(r"^\s+sha512:\s*(.+)$", line)
        if m and cur:
            files[cur] = m.group(1).strip().strip("'\"")
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip("'\"")
            cur = None
    out["files"] = files
    return out


def electron_generic(base_url, local, channel="latest"):
    """electron-builder 'generic' provider: <url>/<channel>-linux.yml."""
    base = base_url.rstrip("/") + "/"
    _, body, _ = http_request(urllib.parse.urljoin(base, f"{channel}-linux.yml"), limit=65536)
    info = _parse_builder_yml(body.decode("utf-8", "replace"))
    version = clean_version(info.get("version"))
    path = info.get("path") or next((f for f in info["files"] if f.lower().endswith(".appimage")), "")
    if version == "unknown" or not path:
        raise AppShipError("electron 更新文件中没有版本信息")
    url = urllib.parse.urljoin(base, path)
    sha = info["files"].get(path) or info.get("sha512", "")
    return Update.compare(
        local,
        version,
        download_url=url if url.startswith("https://") else "",
        filename=os.path.basename(path),
        checksums={"sha512": sha},
        source="electron 更新源",
        kind="appimage",
    )


def zsync_source(url, local):
    _, data, _ = http_request(url, headers={"Range": "bytes=0-8191"}, limit=8192)
    header = data.split(b"\n\n", 1)[0].decode("utf-8", "replace")
    fields = {}
    for line in header.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields.setdefault(k.strip().lower(), v.strip())
    filename = fields.get("filename", "")
    file_url = fields.get("url", "")
    version = version_from_filename(filename)
    if version == "unknown":
        version = version_from_filename(file_url)
    if version == "unknown":
        raise AppShipError("zsync 文件名里没有版本号")
    download = urllib.parse.urljoin(url, file_url) if file_url else (url[:-6] if url.lower().endswith(".zsync") else "")
    return Update.compare(
        local,
        version,
        download_url=download if download.startswith("https://") else "",
        filename=filename,
        checksums={"sha1": fields.get("sha-1", "")},
        source="AppImage zsync",
        kind="appimage",
    )


def resolve_source(src, app):
    """Run a cached/pinned source descriptor."""
    local = app.version
    hint = (app.data.get("history") or [{}])[-1].get("file", "")
    t = src.get("type")
    if t == "github":
        return github_release(
            src["repo"], local, hint=hint, asset_glob=src.get("pattern", ""), allow_prerelease=src.get("prerelease")
        )
    if t == "zsync":
        return zsync_source(src["url"], local)
    if t == "electron-generic":
        return electron_generic(src["url"], local, src.get("channel", "latest"))
    if t == "none":
        return Update.none(src.get("detail", "未找到更新来源"), local=local)
    raise AppShipError(f"未知的更新来源类型: {t}")


# ==============================================================================
# Discovery (AppImage)
# ==============================================================================
_GH_URL_RE = re.compile(rb"github\.com/(?:repos/)?([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})")
_GH_IGNORE_REPOS = {"releases", "issues", "blob", "tree", "raw", "archive", "pull", "wiki"}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _pick_repo(blob, app):
    """Choose a GitHub repo mentioned in `blob` whose name matches the app."""
    names = {_norm(x) for x in (app.id, app.name, app.data.get("folder"), app.data.get("wm_class")) if x}
    names.discard("")
    counts = {}
    for owner, repo in _GH_URL_RE.findall(blob):
        owner, repo = owner.decode(), repo.decode()
        # Binary string tables run strings together ("…/oryxisgithub.com/…"): cut at a glued-on host.
        repo = re.split(r"(?i)(?:github|gitlab)\.com|https?:", repo, 1)[0]
        repo = re.sub(r"(\.git|[.\-_]+)$", "", repo)
        if repo.lower() in _GH_IGNORE_REPOS or not repo:
            continue
        if _norm(repo) in names or any(
            n and (_norm(repo).startswith(n) or n.startswith(_norm(repo))) and len(_norm(repo)) >= 4 for n in names
        ):
            key = f"{owner}/{repo}"
            counts[key] = counts.get(key, 0) + (2 if _norm(repo) in names else 1)
    if not counts:
        return ""
    return max(counts, key=counts.get)


def discover_source(app):
    """Find an update source for an AppImage. Returns a descriptor dict."""
    path = app.exec_path
    info = appimage.read_update_info(path)
    if info:
        parts = [p.strip() for p in info.split("|")]
        if parts[0] == "zsync" and len(parts) >= 2:
            return {"type": "zsync", "url": "|".join(parts[1:]), "via": ".upd_info"}
        if parts[0] in ("gh-releases-zsync", "gh-releases-direct") and len(parts) >= 5:
            pattern = re.sub(r"\.zsync$", "", "|".join(parts[4:]))
            src = {"type": "github", "repo": f"{parts[1]}/{parts[2]}", "via": ".upd_info", "pattern": pattern}
            if parts[3] not in ("latest", "latest-all"):
                src["prerelease"] = True
            return src

    if not appimage.have_7z():
        return {"type": "none", "detail": "未安装 7z，无法读取 AppImage 内容"}
    members = appimage.list_members(path)
    files = {n for n, is_dir, is_link in members if not is_dir}

    yml = next((n for n in ("resources/app-update.yml", "usr/lib/resources/app-update.yml") if n in files), None)
    if yml:
        cfg = _parse_builder_yml(appimage.read_member(path, yml, limit=65536).decode("utf-8", "replace"))
        provider = cfg.get("provider", "")
        if provider == "github" and cfg.get("owner") and cfg.get("repo"):
            src = {"type": "github", "repo": f"{cfg['owner']}/{cfg['repo']}", "via": "app-update.yml"}
            if cfg.get("channel") not in (None, "", "latest"):
                src["prerelease"] = True
            return src
        if provider == "generic" and cfg.get("url", "").startswith("https://"):
            return {
                "type": "electron-generic",
                "url": cfg["url"],
                "channel": cfg.get("channel") or "latest",
                "via": "app-update.yml",
            }

    meta_files = [n for n in files if n.endswith((".desktop", ".metainfo.xml", ".appdata.xml"))]
    blob = b"\n".join(appimage.read_member(path, n, limit=262144) for n in meta_files[:6])
    repo = _pick_repo(blob, app)
    if repo:
        return {"type": "github", "repo": repo, "via": "metainfo"}

    for exe in _main_executables(files, members, app):
        repo = _pick_repo(appimage.read_member(path, exe, limit=320 * 1024 * 1024), app)
        if repo:
            return {"type": "github", "repo": repo, "via": f"binary:{exe}"}
    return {"type": "none", "detail": "未找到内嵌更新信息或 GitHub 仓库"}


def _main_executables(files, members, app):
    links = {n for n, _d, is_link in members if is_link}
    names = {_norm(x) for x in (app.id, app.name, app.data.get("folder")) if x}
    bins = [n for n in files if n.startswith("usr/bin/") and n.count("/") == 2 and n not in links]
    preferred = [b for b in bins if _norm(os.path.basename(b)) in names]
    rest = [b for b in bins if b not in preferred]
    extra = [n for n in ("AppRun.wrapped",) if n in files and n not in links]
    return (preferred + rest + extra)[:3]


exec_signature = appimage.exec_signature


# ==============================================================================
# Checker
# ==============================================================================
class UpdateChecker:
    def __init__(self, registry, plugins):
        self.registry = registry
        self.plugins = plugins

    def check_app(self, app):
        """Returns (Update, cache_fields_or_None)."""
        plugin = self.plugins.for_app(app)
        if plugin and hasattr(plugin.module, "check"):
            try:
                res = plugin.module.check(app)
            except AppShipError as exc:
                return Update.error(str(exc), local=app.version, source=f"插件 {plugin.name}"), None
            except Exception as exc:  # plugin bug: report, don't crash the run
                return Update.error(f"插件 {plugin.name} 出错: {exc!r}", local=app.version), None
            if res is not None:
                if not res.source:
                    res.source = f"插件 {plugin.name}"
                return res, None

        if app.kind != "appimage":
            return Update.none(f"没有插件提供该 {app.kind} 应用的更新来源", local=app.version), None
        if not app.installed:
            return Update.error("找不到已安装的 AppImage", local=app.version), None

        src = app.data.get("update_source")
        sig = exec_signature(app.exec_path)
        cache = None
        if not src or (not app.data.get("source_pinned") and app.data.get("source_sig") != sig):
            try:
                src = discover_source(app)
            except Exception as exc:
                return Update.error(f"探测更新来源失败: {exc}", local=app.version), None
            cache = {"update_source": src, "source_sig": sig}
        try:
            res = resolve_source(src, app)
        except AppShipError as exc:
            res = Update.error(str(exc), local=app.version)
            if not app.data.get("source_pinned"):
                cache = {"update_source": src, "source_sig": ""}  # retry discovery next time
        if src.get("type") == "github" and not res.source:
            res.source = f"GitHub ({src['repo']})"
        if src.get("via") and res.source and src["type"] != "none":
            res.source = f"{res.source} ← {src['via']}"
        return res, cache

    def check_many(self, apps, workers=6):
        if not apps:
            return []
        with ThreadPoolExecutor(max_workers=min(workers, len(apps))) as pool:
            outcomes = list(pool.map(self.check_app, apps))
        rows = []
        for app, (res, cache) in zip(apps, outcomes):
            if cache:
                self.registry.db[app.id].update(cache)
            rows.append((app, res))
        self.registry.save()  # also persists plugin-side caches stored in app.data
        return rows

    def report(self, query=None, verbose=False):
        if query:
            app = self.registry.find(query)
            if not app:
                print(f"❌ 未找到应用: {query}\n")
                return []
            apps = [app]
        else:
            apps = self.registry.apps()
        if not apps:
            print("\n  (暂无通过该脚本管理的应用)\n")
            return []

        print(f"\n🔍 正在检查 {len(apps)} 个应用的上游版本...")
        rows = self.check_many(apps)
        print(f"\n  {pad('应用 ID', 22)} {pad('当前版本', 18)} {pad('最新版本', 18)} {pad('状态', 12)} 来源")
        print("  " + "-" * 104)
        for app, res in rows:
            remote = res.remote or "—"
            label = STATUS_LABEL.get(res.status, res.status)
            print(f"  {pad(app.id, 22)} {pad(res.local, 18)} {pad(remote, 18)} {pad(label, 12)} {res.source or ''}")

        updates = [(a, r) for a, r in rows if r.status == "update"]
        problems = [(a, r) for a, r in rows if r.status in ("error", "none") and r.detail]
        if updates:
            print("\n⬇ 可更新:")
            for app, res in updates:
                print(f"  {app.name}  {res.local} → {res.remote}")
                if res.download_url:
                    print(f"    {res.download_url}")
            print("\n💡 运行 'appship upgrade' 自动下载并升级（或 'appship upgrade <App ID>' 只升级一个）。")
        if problems and (verbose or any(r.status == "error" for _, r in problems)):
            print("\nℹ️  说明:")
            for app, res in problems:
                if verbose or res.status == "error":
                    print(f"  • {app.name}: {res.detail}")
        if any(r.status == "none" for _, r in rows):
            print(
                f"\n💡 无更新来源的应用可写插件 ({PLUGINS_DIR}/) 或用 'appship set-source <App ID> github:owner/repo' 指定。"
            )
        print()
        return rows
