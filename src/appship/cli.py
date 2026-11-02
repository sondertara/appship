"""Command-line interface: explicit subcommands, long options, machine-readable output."""

import argparse
import glob
import json
import os
import platform
import re
import shutil
import sys

from . import __version__, appimage
from .checks import UpdateChecker
from .config import (
    APPLICATIONS_DIR,
    CACHE_DIR,
    DEB_DIR,
    DOWNLOAD_CACHE,
    DOWNLOADS_DIR,
    ICONS_DIR,
    LOCAL_DIR,
    PLUGINS_DIR,
    RESERVED_LOCAL_NAMES,
    USE_LEGACY_LAYOUT,
    VERSIONS_DB_PATH,
)
from .installer import Installer, file_kind
from .migrate import migrate
from .plugins import PluginManager
from .registry import AppRegistry
from .repair import repair
from .util import AppShipError, format_size, http_head

EPILOG = f"""示例:
  appship install Foo.AppImage
  appship scan ~/Downloads
  appship list
  appship check
  appship upgrade antigravity
  appship uninstall antigravity
  appship rollback antigravity
  appship history antigravity
  appship repair
  appship set-source antigravity github:owner/repo
  appship adopt codebuddy-cn --plugin codebuddy
  appship plugins
  appship doctor
  appship migrate --dry-run
  appship migrate --apply

目录:
  应用:      {LOCAL_DIR}
  插件:      {PLUGINS_DIR}
  deb 构建:  {DEB_DIR}/<包名>/
"""


class ArgParser(argparse.ArgumentParser):
    def error(self, message):
        if "unrecognized arguments" in message:
            sys.stderr.write(f"\n❌ 命令参数错误: {message}\n")
            if "-u" in message or "-U" in message:
                sys.stderr.write("💡 卸载用 'appship uninstall APP'，升级用 'appship upgrade [APP]'。\n")
            sys.stderr.write("💡 名称含空格请用引号包裹，或直接使用无空格的应用 ID。\n\n")
            sys.exit(2)
        super().error(message)


class Context:
    """Lazy core objects: help/version never touch the registry."""

    def __init__(self, no_plugins=False):
        self.no_plugins = no_plugins
        self._registry = None
        self._plugins = None
        self._installer = None
        self._checker = None
        self._synced = False

    def _sync(self):
        if not self._synced and self._plugins is not None and self._registry is not None:
            self._plugins.sync_registry(self._registry)
            self._synced = True

    @property
    def registry(self):
        if self._registry is None:
            self._registry = AppRegistry()
        self._sync()
        return self._registry

    @property
    def plugins(self):
        if self._plugins is None:
            self._plugins = PluginManager(load_plugins=not self.no_plugins)
            self._plugins.warn_errors()
        self._sync()
        return self._plugins

    @property
    def installer(self):
        if self._installer is None:
            self._installer = Installer(self.registry, self.plugins)
        return self._installer

    @property
    def checker(self):
        if self._checker is None:
            self._checker = UpdateChecker(self.registry, self.plugins)
        return self._checker


def scan_dir(directory, include_deb=True):
    exts = ("*.AppImage", "*.appimage") + (("*.deb",) if include_deb else ())
    out = set()
    for pat in exts:
        out.update(glob.glob(os.path.join(directory, pat)))
    return sorted(p for p in out if os.path.isfile(p) and os.path.basename(p) not in RESERVED_LOCAL_NAMES)


def set_source(registry, query, spec):
    app = registry.find(query)
    if not app:
        raise AppShipError(f"未找到应用: {query}")
    spec = spec.strip()
    if spec == "auto":
        registry.db[app.id].pop("update_source", None)
        registry.db[app.id].pop("source_sig", None)
        registry.db[app.id].pop("source_pinned", None)
        registry.save()
        print(f"✅ {app.name}: 已恢复自动探测更新来源")
        return
    m = re.fullmatch(r"(?:github:|https://github\.com/)?([\w.-]+/[\w.-]+?)(?:\.git)?/?", spec)
    if spec.startswith("zsync:"):
        src = {"type": "zsync", "url": spec[6:], "via": "手动指定"}
    elif m:
        src = {"type": "github", "repo": m.group(1), "via": "手动指定"}
    else:
        raise AppShipError("来源格式: github:owner/repo | https://github.com/owner/repo | zsync:URL | auto")
    registry.update(app.id, update_source=src, source_pinned=True, source_sig="")
    print(f"✅ {app.name}: 更新来源已设为 {src}")


# ==============================================================================
# JSON views
# ==============================================================================
def _app_json(app):
    data = app.data
    backup = data.get("backup_pkg") if app.kind != "appimage" else data.get("backup_exec_path")
    return {
        "id": app.id,
        "name": app.name,
        "kind": app.kind,
        "version": app.version,
        "installed": app.installed,
        "installed_at": data.get("installed_at", ""),
        "exec_path": app.exec_path,
        "source": data.get("update_source"),
        "rollback_available": bool(backup and os.path.exists(backup)),
    }


def _update_json(app, res):
    return {
        "id": app.id,
        "name": app.name,
        "kind": app.kind,
        "local": res.local,
        "remote": res.remote,
        "status": res.status,
        "source": res.source,
        "detail": res.detail,
        "download_url": res.download_url,
        "filename": res.filename,
        "update_kind": res.kind,
        "checksums": res.checksums,
        "force": res.force,
    }


# ==============================================================================
# Commands
# ==============================================================================
def cmd_install(args, ctx):
    targets = []
    for raw in args.files:
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isfile(path):
            raise AppShipError(f"找不到文件: {path}")
        if not ctx.installer.supports_file(path):
            if path.lower().endswith(".deb"):
                backend = ctx.plugins.get("deb")
                reason = backend.unavailable_reason if backend else ""
                raise AppShipError(f"deb backend 不可用: {reason or '需要 pacman/makepkg/fakeroot（仅 Arch 系）'}")
            raise AppShipError(f"不支持的文件类型: {path}")
        targets.append(path)
    if any(file_kind(t) == "appimage" for t in targets) and not appimage.have_7z():
        print("⚠️  未检测到 7z 命令，AppImage 图标与元数据解析将受限 (sudo pacman -S 7zip)", file=sys.stderr)
    ctx.installer.process_files(targets, dry_run=args.dry_run, force=args.force, auto_yes=args.yes)
    return 0


def cmd_scan(args, ctx):
    directory = os.path.abspath(os.path.expanduser(args.dir or DOWNLOADS_DIR))
    if not os.path.isdir(directory):
        raise AppShipError(f"目录不存在: {directory}")
    include_deb = ctx.plugins.backend("deb") is not None
    targets = scan_dir(directory, include_deb=include_deb)
    if not targets:
        suffix = " / deb" if include_deb else ""
        print(f"🔍 在 {directory} 未发现 AppImage{suffix} 文件。")
        return 0
    print(f"📥 从 {directory} 读取到 {len(targets)} 个文件...")
    if any(file_kind(t) == "appimage" for t in targets) and not appimage.have_7z():
        print("⚠️  未检测到 7z 命令，AppImage 图标与元数据解析将受限 (sudo pacman -S 7zip)", file=sys.stderr)
    ctx.installer.process_files(targets, dry_run=args.dry_run, force=args.force, auto_yes=args.yes)
    return 0


def cmd_list(args, ctx):
    _ = ctx.plugins
    apps = ctx.registry.apps()
    if args.json:
        print(json.dumps([_app_json(a) for a in apps], indent=2, ensure_ascii=False))
    elif args.quiet:
        for app in apps:
            print(app.id)
    else:
        ctx.registry.list_apps()
    return 0


def cmd_check(args, ctx):
    quiet_find = args.json or args.quiet
    if args.app:
        app = ctx.registry.find(args.app, quiet=quiet_find)
        if not app:
            if args.json:
                print(json.dumps({"error": f"未找到应用: {args.app}"}, ensure_ascii=False))
            else:
                print(f"❌ 未找到应用: {args.app}", file=sys.stderr)
            return 1
        apps = [app]
    else:
        apps = ctx.registry.apps()

    if not apps:
        if args.json:
            print("[]")
        elif not args.quiet:
            print("\n  (暂无通过该脚本管理的应用)\n")
        return 0

    if args.json or args.quiet:
        rows = ctx.checker.check_many(apps)
        if args.json:
            print(json.dumps([_update_json(a, r) for a, r in rows], indent=2, ensure_ascii=False))
        else:
            for app, res in rows:
                if res.status == "error":
                    print(f"{app.id}: {res.detail}", file=sys.stderr)
    else:
        rows = ctx.checker.report(args.app, verbose=args.verbose)

    if args.exit_code:
        if any(res.status == "error" for _app, res in rows):
            return 2
        if any(res.status == "update" for _app, res in rows):
            return 1
    return 0


def cmd_upgrade(args, ctx):
    if args.app and not ctx.registry.find(args.app):
        print(f"❌ 未找到应用: {args.app}", file=sys.stderr)
        return 1
    ctx.installer.upgrade(ctx.checker, args.app, dry_run=args.dry_run, auto_yes=args.yes)
    return 0


def cmd_uninstall(args, ctx):
    ctx.installer.uninstall(args.app, dry_run=args.dry_run, keep_data=args.keep_data, auto_yes=args.yes)
    return 0


def cmd_rollback(args, ctx):
    ctx.installer.rollback(args.app, auto_yes=args.yes, dry_run=args.dry_run)
    return 0


def cmd_history(args, ctx):
    _ = ctx.plugins
    if args.app:
        app = ctx.registry.find(args.app, quiet=args.json or args.quiet)
        if not app:
            if args.json:
                print(json.dumps({"error": f"未找到应用: {args.app}"}, ensure_ascii=False))
            else:
                print(f"❌ 未找到应用: {args.app}", file=sys.stderr)
            return 1
        apps = [app]
    else:
        apps = ctx.registry.apps()

    if args.json:
        data = [{"id": a.id, "name": a.name, "version": a.version, "history": a.data.get("history", [])} for a in apps]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif not args.quiet:
        ctx.registry.show_history(args.app)
    return 0


def cmd_repair(args, ctx):
    _ = ctx.plugins
    repair(ctx.registry, args.app)
    ctx.registry.list_apps()
    return 0


def cmd_migrate(args, ctx):
    return migrate(ctx.registry, dry_run=not args.apply, auto_yes=args.yes)


def cmd_set_source(args, ctx):
    set_source(ctx.registry, args.app, args.spec)
    return 0


def cmd_adopt(args, ctx):
    ctx.installer.adopt_pkg(args.pkg, args.plugin or "")
    return 0


def cmd_plugins(args, ctx):
    manager = ctx.plugins
    if args.json:
        data = {
            "directories": manager.plugins_dirs,
            "plugins": manager.status(),
            "errors": [{"file": fname, "error": repr(exc)} for fname, exc in manager.errors],
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif not args.quiet:
        manager.list()
    return 0


def cmd_doctor(args, ctx):
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    add("appship", "ok", __version__)
    add("python", "ok", platform.python_version())
    add(
        "路径布局",
        "warn" if USE_LEGACY_LAYOUT else "ok",
        f"{LOCAL_DIR}（legacy，建议 appship migrate）" if USE_LEGACY_LAYOUT else f"{LOCAL_DIR}（XDG）",
    )

    seven = shutil.which("7z")
    add("7z", "ok" if seven else "warn", seven or "未安装（AppImage 元数据/图标解析受限）")

    for label, path in (("应用目录", APPLICATIONS_DIR), ("图标目录", ICONS_DIR)):
        if os.path.isdir(path):
            add(
                label,
                "ok" if os.access(path, os.W_OK) else "error",
                path if os.access(path, os.W_OK) else f"{path}（不可写）",
            )
        else:
            add(label, "warn", f"{path}（不存在）")

    for label, path in (("缓存目录", CACHE_DIR), ("下载缓存", DOWNLOAD_CACHE)):
        if os.path.isdir(path):
            add(
                label,
                "ok" if os.access(path, os.W_OK) else "error",
                path if os.access(path, os.W_OK) else f"{path}（不可写）",
            )
        else:
            add(label, "warn", f"{path}（不存在，首次使用时创建）")

    if os.path.exists(VERSIONS_DB_PATH):
        add("注册表", "ok", f"{VERSIONS_DB_PATH}（{len(ctx.registry.db)} 个应用）")
    else:
        add("注册表", "warn", f"{VERSIONS_DB_PATH}（不存在）")

    apps = ctx.registry.apps()
    broken = [a.id for a in apps if a.kind == "appimage" and not a.installed]
    uninstalled = [f"{a.kind}:{a.id}" for a in apps if a.kind != "appimage" and not a.installed]
    add(
        "应用完整性",
        "error" if broken else "ok",
        "找不到可执行文件: " + ", ".join(broken) if broken else f"{len(apps)} 个应用的执行路径正常",
    )
    if uninstalled:
        add("后端安装状态", "warn", "未安装: " + ", ".join(uninstalled))
    unknown = [a.id for a in apps if a.version == "unknown"]
    add("版本号", "warn" if unknown else "ok", ("unknown: " + ", ".join(unknown)) if unknown else "全部已知")

    tools = [
        t
        for t in ("update-desktop-database", "gtk-update-icon-cache", "kbuildsycoca6", "kbuildsycoca5")
        if shutil.which(t)
    ]
    add("桌面工具", "ok" if tools else "warn", ", ".join(tools) or "未找到桌面缓存更新工具")

    fuse = os.path.exists("/dev/fuse") and shutil.which("fusermount3")
    add(
        "AppImage FUSE",
        "ok" if fuse else "warn",
        "/dev/fuse + fusermount3" if fuse else "未检测到 FUSE（可用 --appimage-extract-and-run）",
    )

    plugin_errors = ctx.plugins.errors
    add(
        "插件",
        "error" if plugin_errors else "ok",
        f"{len(ctx.plugins.plugins)} 个已加载" + (f"，{len(plugin_errors)} 个加载失败" if plugin_errors else ""),
    )
    for check in ctx.plugins.doctor_checks():
        add(check.get("name", "backend"), check.get("status", "warn"), check.get("detail", ""))

    try:
        free = shutil.disk_usage(LOCAL_DIR).free
        add("磁盘空间", "ok" if free >= 2 * 1024**3 else "warn", f"{format_size(free)} 可用")
    except OSError as exc:
        add("磁盘空间", "warn", str(exc))

    if args.network:
        try:
            headers = http_head("https://github.com", timeout=10)
            add("网络", "ok" if headers else "warn", "https://github.com 可达" if headers else "HEAD 无响应")
        except AppShipError as exc:
            add("网络", "error", str(exc))
    else:
        add("网络", "skip", "未检查（加 --network）")

    if args.json:
        print(
            json.dumps(
                {"appship": __version__, "python": platform.python_version(), "checks": checks},
                indent=2,
                ensure_ascii=False,
            )
        )
    elif not args.quiet:
        print(f"\n🩺 AppShip {__version__} doctor\n")
        icons = {"ok": "✅", "warn": "⚠️", "error": "❌", "skip": "⏭️"}
        for check in checks:
            print(f"  {icons[check['status']]} {check['name']}: {check['detail']}")
        errors = [c for c in checks if c["status"] == "error"]
        print(f"\n{'✅ 全部通过' if not errors else f'❌ {len(errors)} 项需要处理'}\n")
    return 1 if any(c["status"] == "error" for c in checks) else 0


def cmd_help(topic, parser):
    if not topic:
        parser.print_help()
        return 0
    action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    target = action.choices.get(topic) if action else None
    if not target:
        parser.error(f"未知子命令: {topic}")
    target.print_help()
    return 0


# ==============================================================================
# Parser
# ==============================================================================
def build_parser():
    p = ArgParser(
        prog="appship",
        description=f"AppShip {__version__} - AppImage 管理器（deb 通过可选 backend）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    p.add_argument("-v", "--verbose", action="store_true", help="显示更多检查细节")
    p.add_argument("--no-color", action="store_true", help="禁用颜色（NO_COLOR）")
    p.add_argument("--no-plugins", action="store_true", help="禁用内置与用户插件（排障用）")
    p.add_argument("--version", action="version", version=f"appship {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sp = sub.add_parser("install", help="安装一个或多个 AppImage / deb")
    sp.add_argument("files", nargs="+", metavar="FILE")
    sp.add_argument("--force", action="store_true", help="即使版本不高于已安装版本也强制安装")
    sp.add_argument("-n", "--dry-run", action="store_true", help="模拟演练，不改动任何文件")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("scan", help=f"扫描目录并安装（默认 {DOWNLOADS_DIR}）")
    sp.add_argument("dir", nargs="?", metavar="DIR")
    sp.add_argument("--force", action="store_true", help="即使版本不高于已安装版本也强制安装")
    sp.add_argument("-n", "--dry-run", action="store_true", help="模拟演练，不改动任何文件")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("list", help="列出已管理的应用")
    sp.add_argument("--json", action="store_true", help="输出 JSON")
    sp.add_argument("-q", "--quiet", action="store_true", help="只输出应用 ID")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("check", help="检查上游是否有新版本")
    sp.add_argument("app", nargs="?", metavar="APP")
    sp.add_argument("--json", action="store_true", help="输出 JSON")
    sp.add_argument("--exit-code", action="store_true", help="有更新返回 1，检查失败返回 2")
    sp.add_argument("-q", "--quiet", action="store_true", help="不输出正常结果，仅错误写 stderr")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="显示更多检查细节")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("upgrade", help="检查并下载、安装新版本")
    sp.add_argument("app", nargs="?", metavar="APP")
    sp.add_argument("-n", "--dry-run", action="store_true", help="只显示将要升级的内容")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.set_defaults(func=cmd_upgrade)

    sp = sub.add_parser("uninstall", help="卸载并清理")
    sp.add_argument("app", metavar="APP")
    sp.add_argument("-n", "--dry-run", action="store_true", help="只显示将清理的内容")
    sp.add_argument("--keep-data", action="store_true", help="保留用户数据与配置")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("rollback", help="回滚到上一个版本")
    sp.add_argument("app", metavar="APP")
    sp.add_argument("-n", "--dry-run", action="store_true", help="只显示将回滚的内容")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("history", help="查看版本更新历史")
    sp.add_argument("app", nargs="?", metavar="APP")
    sp.add_argument("--json", action="store_true", help="输出 JSON")
    sp.add_argument("-q", "--quiet", action="store_true", help="不输出正常结果")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("repair", help="修复版本号 / 快捷方式等元数据")
    sp.add_argument("app", nargs="?", metavar="APP")
    sp.set_defaults(func=cmd_repair)

    sp = sub.add_parser("migrate", help="把 legacy ~/local 迁移到 XDG 布局")
    sp.add_argument("--apply", action="store_true", help="实际执行（默认仅预览）")
    sp.add_argument("-n", "--dry-run", action="store_true", help="仅预览（默认）")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("set-source", help="设置应用的更新来源")
    sp.add_argument("app", metavar="APP")
    sp.add_argument("spec", metavar="SPEC", help="github:owner/repo | zsync:URL | auto")
    sp.set_defaults(func=cmd_set_source)

    sp = sub.add_parser("adopt", help="登记已由可用 backend 管理的应用")
    sp.add_argument("pkg", metavar="PKG")
    sp.add_argument("--plugin", metavar="NAME")
    sp.set_defaults(func=cmd_adopt)

    sp = sub.add_parser("plugins", help="列出已加载的插件")
    sp.add_argument("--json", action="store_true", help="输出 JSON")
    sp.add_argument("-q", "--quiet", action="store_true", help="不输出正常结果")
    sp.set_defaults(func=cmd_plugins)

    sp = sub.add_parser("doctor", help="诊断环境与安装状态")
    sp.add_argument("--json", action="store_true", help="输出 JSON")
    sp.add_argument("--network", action="store_true", help="检查网络连通性")
    sp.add_argument("-q", "--quiet", action="store_true", help="不输出正常结果，仅返回状态码")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("version", help="打印版本")
    sp.set_defaults(func=None)

    sp = sub.add_parser("help", help="显示帮助")
    sp.add_argument("topic", nargs="?", metavar="COMMAND")
    sp.set_defaults(func=None)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    if any(arg in ("-u", "-U") for arg in argv):
        sys.stderr.write("❌ -u/-U 已移除：卸载用 'appship uninstall APP'，升级用 'appship upgrade [APP]'。\n")
        return 2
    args = parser.parse_args(argv)
    if getattr(args, "no_color", False):
        os.environ["NO_COLOR"] = "1"
    if args.command == "help":
        return cmd_help(args.topic, parser)
    if args.command == "version":
        print(f"appship {__version__}")
        return 0

    ctx = Context(no_plugins=args.no_plugins)
    try:
        return args.func(args, ctx)
    except AppShipError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
