"""Repackage official .deb files as local pacman packages.

Each package gets a build dir (``DEB_DIR/<pkgname>``) holding the generated PKGBUILD, the source
.deb and the built packages. Only the deb's data.tar is used: maintainer scripts (postinst etc. — apt
repos, update-alternatives) are never run. Plugins can adjust the result through hooks
(deb_pkgname / deb_depends / deb_package_extra, see plugins.py).
"""

import glob
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from . import desktop
from .config import DEB_DIR, HOST_ARCH
from .registry import now_str, sanitize_id
from .util import AppShipError, clean_version, confirm, file_digest, path_size

DEB_ARCH_TO_PACMAN = {"amd64": "x86_64", "arm64": "aarch64", "armhf": "armv7h", "i386": "i686", "all": "any"}


# ==============================================================================
# Reading .deb files (ar container + control.tar.*)
# ==============================================================================
def _ar_members(path):
    """{name: (offset, size)} of a Debian ar archive."""
    out = {}
    with open(path, "rb") as f:
        if f.read(8) != b"!<arch>\n":
            raise AppShipError(f"不是有效的 .deb 文件: {os.path.basename(path)}")
        while True:
            hdr = f.read(60)
            if len(hdr) < 60:
                break
            name = hdr[:16].decode("ascii", "replace").strip().rstrip("/")
            size = int(hdr[48:58].decode("ascii").strip() or 0)
            out[name] = (f.tell(), size)
            f.seek(size + (size & 1), os.SEEK_CUR)
    return out


def _read_ar_member(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


def read_control(deb_path):
    """Parsed DEBIAN/control fields plus the list of maintainer scripts."""
    members = _ar_members(deb_path)
    ctrl_name = next((n for n in members if n.startswith("control.tar")), None)
    data_name = next((n for n in members if n.startswith("data.tar")), None)
    if not ctrl_name or not data_name:
        raise AppShipError("deb 中缺少 control.tar 或 data.tar")
    blob = _read_ar_member(deb_path, *members[ctrl_name])
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, ctrl_name)
        with open(p, "wb") as f:
            f.write(blob)
        text, scripts = _extract_control_text(p, ctrl_name)
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += "\n" + line.strip()
        elif ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            fields[key] = val.strip()
    fields["_scripts"] = scripts
    fields["_data_member"] = data_name
    return fields


def _extract_control_text(path, name):
    if name.endswith(".zst"):
        # Python's tarfile handles zstd from 3.14; fall back to bsdtar otherwise.
        try:
            return _control_from_tar(tarfile.open(path, "r:zst"))
        except (tarfile.CompressionError, ValueError, AttributeError):
            proc = subprocess.run(
                ["bsdtar", "-xOf", path, "./control"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            listing = subprocess.run(
                ["bsdtar", "-tf", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            return proc.stdout.decode("utf-8", "replace"), _scripts_from_names(listing.stdout.split())
    return _control_from_tar(tarfile.open(path, "r:*"))


def _control_from_tar(tf):
    with tf:
        names = tf.getnames()
        member = next((m for m in tf.getmembers() if m.name.lstrip("./") == "control"), None)
        if member is None:
            raise AppShipError("deb 的 control.tar 中没有 control 文件")
        return tf.extractfile(member).read().decode("utf-8", "replace"), _scripts_from_names(names)


def _scripts_from_names(names):
    return sorted(
        {
            os.path.basename(n)
            for n in names
            if os.path.basename(n) in ("preinst", "postinst", "prerm", "postrm", "config", "triggers")
        }
    )


def deb_version(fields):
    """Debian 'Version' without epoch/revision, made pacman-legal."""
    v = fields.get("Version", "")
    v = v.split(":", 1)[-1]
    v = re.sub(r"-[^-]*$", "", v) if "-" in v else v
    return pacman_safe_version(clean_version(v))


def pacman_safe_version(v):
    """pkgver may not contain ':', '/', '-' or whitespace."""
    v = re.sub(r"[:/\s]+", ".", v)
    return v.replace("-", "_") or "unknown"


def default_pkgname(fields):
    name = (fields.get("Package") or "app").lower()
    return re.sub(r"[^a-z0-9@._+-]", "-", name).lstrip("-.") or "app"


# ==============================================================================
# Dependency translation (Debian -> Arch), best effort
# ==============================================================================
_DEP_RULES = [
    (r"^libgtk-3-0", "gtk3"),
    (r"^libgtk-4-1", "gtk4"),
    (r"^libnss3", "nss"),
    (r"^libnspr4", "nspr"),
    (r"^libasound2", "alsa-lib"),
    (r"^libxss1", "libxss"),
    (r"^libxkbfile1", "libxkbfile"),
    (r"^libxtst6", "libxtst"),
    (r"^libdrm2", "libdrm"),
    (r"^libgbm1", "mesa"),
    (r"^libxkbcommon0", "libxkbcommon"),
    (r"^libsecret-1-0", "libsecret"),
    (r"^xdg-utils", "xdg-utils"),
    (r"^libnotify4", "libnotify"),
    (r"^libcups2", "libcups"),
    (r"^libatk-bridge2\.0-0|^libatk1\.0-0|^libatspi2\.0-0", "at-spi2-core"),
    (r"^libcairo2", "cairo"),
    (r"^libpango-1\.0-0", "pango"),
    (r"^libglib2\.0-0", "glib2"),
    (r"^libdbus-1-3", "dbus"),
    (r"^libexpat1", "expat"),
    (r"^libudev1", "systemd-libs"),
    (r"^libx11-6", "libx11"),
    (r"^libxcb1", "libxcb"),
    (r"^libxcomposite1", "libxcomposite"),
    (r"^libxdamage1", "libxdamage"),
    (r"^libxext6", "libxext"),
    (r"^libxfixes3", "libxfixes"),
    (r"^libxrandr2", "libxrandr"),
    (r"^libappindicator3-1|^libayatana-appindicator3-1", "libappindicator-gtk3"),
    (r"^libwebkit2gtk-4\.1-0", "webkit2gtk-4.1"),
    (r"^libwebkit2gtk-4\.0-37", "webkit2gtk"),
    (r"^libfuse2", "fuse2"),
    (r"^libvulkan1", "vulkan-icd-loader"),
    (r"^libpulse0", "libpulse"),
    (r"^libcurl", "curl"),
    (r"^libssl3|^libssl1", "openssl"),
    (r"^zlib1g", "zlib"),
]
_DEP_SKIP = re.compile(
    r"^(libc6|libstdc\+\+6|libgcc-s1|libgcc1|ca-certificates|dpkg|debconf|lsb-base|init-system-helpers)\b"
)


def translate_depends(fields):
    """Debian Depends -> Arch package names. Alternatives (a | b) use the first known one."""
    out = []
    for group in re.split(r",\s*", (fields.get("Depends") or "").replace("\n", " ")):
        for alt in group.split("|"):
            name = re.sub(r"\s*\(.*?\)|\s*\[.*?\]|:any", "", alt).strip()
            if not name or _DEP_SKIP.match(name):
                break
            arch = next((a for pat, a in _DEP_RULES if re.match(pat, name)), None)
            if arch:
                if arch not in out:
                    out.append(arch)
                break
    return out


# ==============================================================================
# PKGBUILD generation & build
# ==============================================================================
def _bash_array(items):
    return "(" + " ".join(shlex.quote(i) for i in items) + ")"


def render_pkgbuild(
    pkgname, pkgver, pkgrel, desc, url, arch, depends, deb_filename, sha256, data_member, extra_lines=""
):
    data_cmd = f'bsdtar -xOf "${{srcdir}}/{deb_filename}" {shlex.quote(data_member)} | bsdtar -xpf - -C "${{pkgdir}}"'
    body = f"""# Generated by appship from the official .deb — edits are overwritten on the next upgrade.
# Only data.tar is used; the deb's maintainer scripts are not run.
pkgname={shlex.quote(pkgname)}
pkgver={shlex.quote(pkgver)}
pkgrel={pkgrel}
pkgdesc={shlex.quote(desc)}
arch=({shlex.quote(arch)})
url={shlex.quote(url)}
license=('custom')
depends={_bash_array(depends)}
options=('!strip' '!debug')
source=({shlex.quote(deb_filename)})
noextract=({shlex.quote(deb_filename)})
sha256sums=({shlex.quote(sha256)})

package() {{
    {data_cmd}

    # Debian-only bits pacman has no use for.
    rm -rf "${{pkgdir}}/usr/share/doc" "${{pkgdir}}/usr/share/lintian" "${{pkgdir}}/DEBIAN"
    if [ -d "${{pkgdir}}/usr/share/appdata" ]; then
        install -d "${{pkgdir}}/usr/share/metainfo"
        mv "${{pkgdir}}"/usr/share/appdata/* "${{pkgdir}}/usr/share/metainfo/"
        rmdir "${{pkgdir}}/usr/share/appdata"
    fi

    # bsdtar keeps setuid bits (e.g. Electron's chrome-sandbox) since makepkg runs package() in fakeroot.
"""
    if extra_lines.strip():
        body += "\n    # --- plugin additions ---\n"
        body += "\n".join("    " + line if line.strip() else "" for line in extra_lines.strip("\n").splitlines()) + "\n"
    body += "}\n"
    return body


def built_package(build_dir, pkgname, pkgver, pkgrel):
    pattern = os.path.join(build_dir, f"{pkgname}-{pkgver}-{pkgrel}-*.pkg.tar.*")
    hits = [p for p in glob.glob(pattern) if not p.endswith(".sig")]
    return max(hits, key=os.path.getmtime) if hits else ""


def build_from_deb(deb_path, plugin=None, dry_run=False, force_rebuild=False):
    """Create/refresh ~/local/debpkgs/<pkg>/ and run makepkg. Returns a dict describing the build."""
    for tool in ("makepkg", "bsdtar", "pacman"):
        if not shutil.which(tool):
            raise AppShipError(f"缺少命令: {tool}（deb 安装依赖 pacman/makepkg）")
    fields = read_control(deb_path)
    deb_arch = fields.get("Architecture", "amd64")
    arch = DEB_ARCH_TO_PACMAN.get(deb_arch, deb_arch)
    if arch not in ("any", HOST_ARCH):
        raise AppShipError(f"deb 架构为 {deb_arch}，与本机 {HOST_ARCH} 不符")

    pkgname = default_pkgname(fields)
    pkgver = deb_version(fields)
    depends = translate_depends(fields)
    extra = ""
    if plugin is not None:
        mod = plugin.module
        if hasattr(mod, "deb_pkgname"):
            pkgname = mod.deb_pkgname(fields) or pkgname
        if hasattr(mod, "deb_pkgver"):
            pkgver = pacman_safe_version(mod.deb_pkgver(fields, os.path.basename(deb_path)) or pkgver)
        if hasattr(mod, "deb_depends"):
            depends = list(mod.deb_depends(fields, depends))
        if hasattr(mod, "deb_package_extra"):
            extra = mod.deb_package_extra(fields) or ""

    build_dir = os.path.join(DEB_DIR, pkgname)
    desc = (fields.get("Description") or pkgname).split("\n", 1)[0].strip() + " (repacked from official .deb)"
    url = fields.get("Homepage") or "https://example.invalid"
    deb_filename = re.sub(r"[^\w.+\-]", "_", os.path.basename(deb_path))
    if not deb_filename.endswith(".deb"):
        deb_filename += ".deb"
    sha256 = file_digest(deb_path, "sha256")

    info = {
        "pkgname": pkgname,
        "pkgver": pkgver,
        "pkgrel": 1,
        "arch": arch,
        "depends": depends,
        "build_dir": build_dir,
        "deb_filename": deb_filename,
        "sha256": sha256,
        "fields": fields,
        "name": (fields.get("Package") or pkgname),
        "package_file": "",
    }
    if dry_run:
        return info

    os.makedirs(build_dir, exist_ok=True)
    pkgrel = _next_pkgrel(build_dir, pkgname, pkgver, sha256)
    info["pkgrel"] = pkgrel
    existing = built_package(build_dir, pkgname, pkgver, pkgrel)
    if existing and not force_rebuild:
        info["package_file"] = existing
        return info

    # Keep only the deb being built; older debs are replaced to save space.
    for old in glob.glob(os.path.join(build_dir, "*.deb")):
        if os.path.basename(old) != deb_filename:
            os.remove(old)
    dest_deb = os.path.join(build_dir, deb_filename)
    if os.path.abspath(deb_path) != os.path.abspath(dest_deb):
        shutil.copy2(deb_path, dest_deb)

    with open(os.path.join(build_dir, "PKGBUILD"), "w", encoding="utf-8") as f:
        f.write(
            render_pkgbuild(
                pkgname, pkgver, pkgrel, desc, url, arch, depends, deb_filename, sha256, fields["_data_member"], extra
            )
        )
    with open(os.path.join(build_dir, ".source-sha256"), "w") as f:
        f.write(f"{pkgver}-{pkgrel} {sha256}\n")

    print(f"   🔨 makepkg 构建 {pkgname} {pkgver}-{pkgrel} ...")
    proc = subprocess.run(
        ["makepkg", "-f", "--noconfirm", "--nodeps", "--cleanbuild"],
        cwd=build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.strip().splitlines()[-15:])
        raise AppShipError(f"makepkg 失败:\n{tail}")
    for d in ("src", "pkg"):
        shutil.rmtree(os.path.join(build_dir, d), ignore_errors=True)
    pkgfile = built_package(build_dir, pkgname, pkgver, pkgrel)
    if not pkgfile:
        raise AppShipError("makepkg 成功但没有找到生成的包文件")
    info["package_file"] = pkgfile
    return info


def _next_pkgrel(build_dir, pkgname, pkgver, sha256):
    """Same version + same deb -> same pkgrel; same version but a different deb -> bump pkgrel."""
    marker = os.path.join(build_dir, ".source-sha256")
    try:
        with open(marker) as f:
            prev_ver, prev_sha = f.read().split()
    except (OSError, ValueError):
        return 1
    prev_pkgver, _, prev_rel = prev_ver.rpartition("-")
    if prev_pkgver != pkgver:
        return 1
    rel = int(prev_rel) if prev_rel.isdigit() else 1
    return rel if prev_sha == sha256 else rel + 1


def prune_packages(build_dir, keep=2):
    """Keep the newest `keep` built packages (current + one rollback target)."""
    pkgs = sorted(
        (p for p in glob.glob(os.path.join(build_dir, "*.pkg.tar.*")) if not p.endswith(".sig")),
        key=os.path.getmtime,
        reverse=True,
    )
    for p in pkgs[keep:]:
        try:
            os.remove(p)
        except OSError:
            pass


def pacman_install(pkgfile, auto_yes=False):
    """Run `sudo pacman -U` (interactive unless auto_yes)."""
    cmd = ["sudo", "pacman", "-U"]
    if auto_yes:
        cmd.append("--noconfirm")
    cmd.append(pkgfile)
    print(f"   🔑 需要管理员权限: {' '.join(shlex.quote(c) for c in cmd)}")
    return subprocess.run(cmd).returncode == 0


def pacman_remove(pkgname, auto_yes=False):
    cmd = ["sudo", "pacman", "-R"]
    if auto_yes:
        cmd.append("--noconfirm")
    cmd.append(pkgname)
    print(f"   🔑 需要管理员权限: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode == 0


def pacman_owned_files(pkgname):
    proc = subprocess.run(["pacman", "-Qlq", pkgname], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return [line for line in proc.stdout.splitlines() if line]


def package_install_prefixes(pkgname):
    """Top-level app dirs a package owns (e.g. /usr/share/buddycn, /opt/foo) — used to find running processes."""
    files = pacman_owned_files(pkgname)
    roots = set()
    for f in files:
        m = re.match(r"^(/opt/[^/]+|/usr/share/[^/]+|/usr/lib/[^/]+)/", f)
        if m and m.group(1).split("/")[-1] not in (
            "applications",
            "icons",
            "pixmaps",
            "mime",
            "metainfo",
            "licenses",
            "doc",
            "man",
            "locale",
        ):
            roots.add(m.group(1))
    return sorted(roots)


# ==============================================================================
# Backend plugin surface
# ==============================================================================
_REQUIRED_TOOLS = ("pacman", "makepkg", "bsdtar", "fakeroot")


def _missing_tools():
    missing = [tool for tool in _REQUIRED_TOOLS if not shutil.which(tool)]
    if os.geteuid() != 0 and not shutil.which("sudo"):
        missing.append("sudo")
    return missing


def available():
    """True on Arch-family systems with the full deb→pacman toolchain."""
    return not _missing_tools()


def unavailable_reason():
    missing = _missing_tools()
    if not missing:
        return ""
    return "缺少命令: " + ", ".join(missing)


def match_file(path, info=None):
    return str(path).lower().endswith(".deb") and available()


def pacman_installed_versions(names):
    names = [n for n in names if n]
    if not names or not shutil.which("pacman"):
        return {}
    proc = subprocess.run(["pacman", "-Q", *names], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    found = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[parts[0]] = parts[1]
    return found


def pacman_to_upstream_version(pkgver):
    v = pkgver.split(":", 1)[-1]
    return v.rsplit("-", 1)[0] if "-" in v else v


def analyze(installer, path):
    """Parse a .deb and return the core analyse tuple."""
    fields = read_control(path)
    plugin = installer.plugins.for_file(path, {"kind": "deb", "fields": fields}, include_backends=False)
    pkgname = default_pkgname(fields)
    version = deb_version(fields)
    if plugin is not None:
        pkgname = plugin.call("deb_pkgname", fields) or pkgname
        version = pacman_safe_version(plugin.call("deb_pkgver", fields, os.path.basename(path)) or version)
    existing = installer.registry.find_by_pkg(pkgname)
    app_id = existing.id if existing else sanitize_id(pkgname)
    return "deb", app_id, version, {"fields": fields, "pkgname": pkgname}, plugin


def install(installer, deb_path, meta=None, plugin=None, dry_run=False, auto_yes=False, consume=False):
    if plugin is None:
        fields = read_control(deb_path)
        plugin = installer.plugins.for_file(deb_path, {"kind": "deb", "fields": fields}, include_backends=False)
    info = build_from_deb(deb_path, plugin=plugin, dry_run=True)
    pkgname, pkgver = info["pkgname"], info["pkgver"]
    existing = installer.registry.find_by_pkg(pkgname)
    installed = pacman_installed_versions([pkgname]).get(pkgname, "")
    app_name = (plugin.call("deb_app_name", info["fields"]) if plugin else None) or info["name"]
    status = (
        f"🔄 升级 ({pacman_to_upstream_version(installed)} -> {pkgver})"
        if installed
        else f"✨ 首次安装 (版本: {pkgver})"
    )
    print(f"\n📦 正在处理: {app_name}  [deb → pacman 包 {pkgname}]")
    print(f"   文件来源: {os.path.basename(deb_path)}")
    print(f"   执行状态: {status}")
    print(f"   构建目录: {info['build_dir']}")
    if plugin:
        print(f"   🧩 插件: {plugin.name}")
    if info["fields"].get("_scripts"):
        print(f"   ℹ️  跳过 deb 维护脚本: {', '.join(info['fields']['_scripts'])}")
    if dry_run:
        print(f"   [Dry-run] 依赖: {' '.join(info['depends']) or '(无)'}")
        return

    prev_pkgfile = existing.data.get("package_file") if existing else ""
    info = build_from_deb(deb_path, plugin=plugin)
    pkgfile = info["package_file"]
    print(f"   📦 已生成: {os.path.basename(pkgfile)}")
    if not pacman_install(pkgfile, auto_yes=auto_yes):
        raise AppShipError("pacman 安装未完成（已取消或失败），包文件保留在构建目录中")

    app_id = existing.id if existing else sanitize_id(pkgname)
    installer.registry.register(
        app_id,
        app_name,
        pkgver,
        os.path.basename(deb_path),
        kind="deb",
        pacman_pkg=pkgname,
        deb_dir=info["build_dir"],
        package_file=pkgfile,
        backup_pkg=prev_pkgfile if (prev_pkgfile and prev_pkgfile != pkgfile and os.path.exists(prev_pkgfile)) else "",
        plugin=plugin.name if plugin else "",
    )
    prune_packages(info["build_dir"], keep=2)
    if consume and os.path.abspath(os.path.dirname(deb_path)) != os.path.abspath(info["build_dir"]):
        try:
            os.remove(deb_path)
        except OSError:
            pass
    desktop.refresh_desktop()
    print(f"   ✅ {app_name} {pkgver} 已通过 pacman 安装")
    desktop.notify("AppShip", f"🎉 {app_name} 已就绪 (v{pkgver})", icon=pkgname)


def uninstall(installer, app, dry_run=False, keep_data=False, auto_yes=False):
    d = app.data
    pkg = app.pacman_pkg or app.id
    installed = bool(pacman_installed_versions([pkg]))
    prefixes = package_install_prefixes(pkg) if installed else []
    pids = desktop.running_pids(exe_prefixes=prefixes) if prefixes else []
    if pids:
        print(f"⚠️ 注意: '{app.name}' 似乎正在运行 (PID: {', '.join(pids)})。")
    print(f"\n📦 pacman 包: {pkg} ({'已安装' if installed else '未安装'})，将执行 sudo pacman -R {pkg}")

    build_dir = d.get("deb_dir") or os.path.join(DEB_DIR, pkg)
    app_files = []
    if os.path.isdir(build_dir) and os.path.commonpath([DEB_DIR, build_dir]) == DEB_DIR and build_dir != DEB_DIR:
        app_files.append(
            {
                "path": build_dir,
                "size": path_size(build_dir),
                "is_dir": True,
                "category": "构建目录 (PKGBUILD / deb / 包文件)",
            }
        )
    data_files = installer._data_files(app, keep_data, app.name, pkg, app.id, d.get("wm_class", ""))
    sections = [
        ("📦 1. appship 构建目录:", app_files),
        ("🗄️  2. 应用工作数据、配置与缓存:" + (" (已指定 --keep-data，跳过)" if keep_data else ""), data_files),
    ]

    def remove_pkg():
        if not installed:
            return True
        if pacman_remove(pkg, auto_yes=auto_yes):
            return True
        print("❌ pacman 卸载未完成，已停止后续清理。")
        return False

    installer._confirm_and_purge(app, sections, pids, dry_run, auto_yes, pre_remove=remove_pkg)


def rollback(installer, app, auto_yes=False, dry_run=False):
    backup = app.data.get("backup_pkg")
    if not backup or not os.path.exists(backup):
        print(f"❌ '{app.name}' 没有可回滚的旧版本包。")
        return
    history = list(app.data.get("history", []))
    prev_version = next(
        (h["version"] for h in reversed(history) if h.get("version") != app.version),
        "previous",
    )
    print(f"⏪ 正在将 '{app.name}' 回滚到 {os.path.basename(backup)} ...")
    if dry_run:
        print(f"   [Dry-run] 将执行 pacman -U {backup}")
        return
    if not confirm(
        f"❓ 确认回滚 '{app.name}' 到 {os.path.basename(backup)}?",
        default=False,
        auto_yes=auto_yes,
    ):
        print("❌ 已取消。")
        return
    if not pacman_install(backup, auto_yes=auto_yes):
        print("❌ pacman 安装未完成，回滚取消。")
        return
    m = re.search(r"-([^-]+)-\d+-[^-]+\.pkg\.tar\.[a-z0-9]+$", os.path.basename(backup))
    if m:
        prev_version = m.group(1)
    fields = {"package_file": backup, "backup_pkg": app.data.get("package_file", "")}
    history.append({"version": prev_version, "date": now_str(), "file": "(rollback)"})
    installer.registry.update(app.id, version=prev_version, history=history, **fields)
    desktop.refresh_desktop()
    print(f"✅ 回滚成功！当前版本: {prev_version}（再执行一次 'appship rollback {app.id}' 可撤销）")


def adopt(installer, pkgname, plugin_name=""):
    installed = pacman_installed_versions([pkgname])
    if pkgname not in installed:
        raise AppShipError(f"pacman 中没有安装 {pkgname}")
    if installer.registry.find_by_pkg(pkgname):
        print(f"ℹ️  {pkgname} 已在 appship 中登记。")
        return
    plugin = installer.plugins.get(plugin_name) if plugin_name else None
    version = pacman_to_upstream_version(installed[pkgname])
    build_dir = os.path.join(DEB_DIR, pkgname)
    pkgfile = (
        built_package(build_dir, pkgname, *installed[pkgname].split(":", 1)[-1].rsplit("-", 1))
        if os.path.isdir(build_dir)
        else ""
    )
    name = pkgname
    if plugin and getattr(plugin.module, "APP_NAME", ""):
        name = plugin.module.APP_NAME
    installer.registry.register(
        sanitize_id(pkgname),
        name,
        version,
        "",
        kind="deb",
        pacman_pkg=pkgname,
        deb_dir=build_dir,
        package_file=pkgfile,
        backup_pkg="",
        plugin=plugin.name if plugin else "",
    )
    print(f"✅ 已登记 {name} ({pkgname} {version})")


def sync_registry(registry):
    debs = {k: v for k, v in registry.db.items() if v.get("kind") == "deb"}
    if not debs:
        return False
    installed = pacman_installed_versions([v.get("pacman_pkg") or k for k, v in debs.items()])
    changed = False
    for app_id, data in debs.items():
        pkg = data.get("pacman_pkg") or app_id
        now_installed = pkg in installed
        if data.get("pkg_installed") != now_installed:
            data["pkg_installed"] = now_installed
            changed = True
        if now_installed:
            version = pacman_to_upstream_version(installed[pkg])
            if data.get("version") != version:
                data["version"] = version
                changed = True
    return changed


def doctor_checks():
    missing = _missing_tools()
    if not missing:
        return [{"name": "deb backend", "status": "ok", "detail": "pacman/makepkg/bsdtar/fakeroot 可用"}]
    return [{"name": "deb backend", "status": "warn", "detail": "不可用: " + ", ".join(missing)}]
