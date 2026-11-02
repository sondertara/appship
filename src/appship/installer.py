"""Install / upgrade / rollback / uninstall orchestration for AppImages and repackaged .debs."""

import glob
import os
import re
import shutil
from functools import cmp_to_key

from . import appimage, desktop
from .config import APPLICATIONS_DIR, DOWNLOAD_CACHE, ICONS_DIR, LOCAL_DIR
from .registry import now_str, sanitize_folder_name, sanitize_id
from .util import AppShipError, compare_versions, confirm, download, format_size, path_size


def file_kind(path):
    return "appimage" if path.lower().endswith(".appimage") else ""


class Installer:
    def __init__(self, registry, plugins):
        self.registry = registry
        self.plugins = plugins

    # ==================================================================
    # Redundant files
    # ==================================================================
    def offer_delete(self, path, reason, dry_run=False, auto_yes=False):
        if not os.path.exists(path):
            return
        size = format_size(path_size(path))
        print(f"   💡 {reason}")
        print(f"      文件位置: {path} (占用空间: {size})")
        if dry_run:
            print("      🔍 [Dry-run] 跳过清理冗余文件。")
            return
        if confirm(f"   🗑️  是否删除该冗余安装包以节省 {size} 磁盘空间?", default=False, auto_yes=auto_yes):
            try:
                os.remove(path)
                print(f"      ✅ 已清理: {os.path.basename(path)}")
            except OSError as e:
                print(f"      ⚠️ 清理失败: {e}")
        else:
            print("      ℹ️  已保留该安装包文件。")

    # ==================================================================
    # Incoming files (batch)
    # ==================================================================
    def analyse(self, path):
        """(kind, app_id, version, meta, plugin) for an incoming file."""
        kind = file_kind(path)
        if kind == "appimage":
            meta = appimage.extract_metadata(path)
            plugin = self.plugins.for_file(path, {"kind": "appimage", "meta": meta})
            return kind, sanitize_id(meta["app_name"]), meta["version"], meta, plugin
        backend = self.plugins.backend_for_file(path, {})
        if backend is None:
            raise AppShipError(f"没有可用的 backend 处理: {os.path.basename(path)}")
        return backend.call("analyze", self, path, required=True)

    def supports_file(self, path):
        if file_kind(path):
            return True
        return self.plugins.backend_for_file(path, {}) is not None

    def process_files(self, targets, dry_run=False, force=False, auto_yes=False):
        if not targets:
            return
        print(f"🚀 发现 {len(targets)} 个待处理的安装包，正在预分析应用信息与版本...")
        groups = {}
        for path in targets:
            try:
                kind, app_id, version, meta, plugin = self.analyse(path)
            except Exception as e:
                print(f"⚠️ 解析 {os.path.basename(path)} 出错: {e}")
                continue
            groups.setdefault(app_id, []).append((version, path, kind, meta, plugin))

        chosen = []
        for app_id, group in groups.items():
            group.sort(key=cmp_to_key(lambda a, b: compare_versions(a[0], b[0])), reverse=True)
            chosen.append((app_id, group[0]))
            if len(group) > 1:
                versions = ", ".join(f"v{g[0]}" for g in group)
                print(f"\n📦 应用 [{app_id}] 有 {len(group)} 个待安装文件 ({versions}):")
                print(f"   🌟 锁定最新版本: v{group[0][0]} ({os.path.basename(group[0][1])})")
                for older in group[1:]:
                    print(f"   ⏭️ 跳过较旧版本: v{older[0]} ({os.path.basename(older[1])})")
                    self.offer_delete(older[1], f"存在更新的安装包 v{group[0][0]}", dry_run, auto_yes)

        todo = []
        for app_id, (version, path, kind, meta, plugin) in chosen:
            current = self.registry.get(app_id)
            cur_ver = current.version if current else ""
            if current and current.installed and not force and cur_ver != "unknown" and version != "unknown":
                cmp_res = compare_versions(version, cur_ver)
                if cmp_res < 0:
                    print(f"\n⚠️  跳过降级: [{current.name}] 待处理 v{version} 低于已安装 v{cur_ver}")
                    self.offer_delete(path, f"待安装包为旧版本 (系统已安装 v{cur_ver})", dry_run, auto_yes)
                    continue
                if cmp_res == 0:
                    print(f"\nℹ️  已是最新: [{current.name}] 已安装同版本 v{cur_ver}")
                    self.offer_delete(path, "系统已存在相同版本，此安装包完全冗余", dry_run, auto_yes)
                    continue
            todo.append((path, kind, meta, plugin))

        if not todo:
            print("\n✨ 所有扫描的应用均已就绪，没有需要安装或升级的应用。")
            return
        print(f"\n⚙️  开始安装/升级 ({len(todo)} 款应用)...")
        for path, kind, meta, plugin in todo:
            try:
                if kind == "appimage":
                    self.install_appimage(path, meta, dry_run=dry_run)
                else:
                    backend = self.plugins.backend(kind)
                    if backend is None:
                        raise AppShipError(f"backend 不可用: {kind}")
                    backend.call(
                        "install",
                        self,
                        path,
                        meta=meta,
                        plugin=plugin,
                        dry_run=dry_run,
                        auto_yes=auto_yes,
                        consume=True,
                        required=True,
                    )
            except AppShipError as e:
                print(f"❌ 安装 {os.path.basename(path)} 失败: {e}")
            except Exception as e:
                print(f"❌ 安装 {os.path.basename(path)} 时出错: {e!r}")
        if not dry_run:
            desktop.refresh_desktop()
            print("\n🎉 全部处理完毕！系统应用菜单与图标缓存已刷新。")

    # ==================================================================
    # AppImage
    # ==================================================================
    def install_appimage(self, app_path, meta, dry_run=False):
        folder = sanitize_folder_name(meta["app_name"])
        app_id = sanitize_id(meta["app_name"])
        app_dir = os.path.join(LOCAL_DIR, folder)
        target = os.path.join(app_dir, f"{folder}.AppImage")
        backup = os.path.join(app_dir, f"{folder}.AppImage.old")
        desktop_file = os.path.join(APPLICATIONS_DIR, f"{app_id}.desktop")
        prev = self.registry.get(app_id)
        old_version = prev.version if prev else "未知"
        status = (
            f"🔄 升级 ({old_version} -> {meta['version']})"
            if (prev or os.path.exists(app_dir))
            else f"✨ 首次安装 (版本: {meta['version']})"
        )
        print(f"\n📦 正在处理: {meta['app_name']}  [AppImage]")
        print(f"   文件来源: {os.path.basename(app_path)}")
        print(f"   执行状态: {status}")
        print(f"   主程序路径: {target}")
        if dry_run:
            print("   [Dry-run] 跳过实际文件写入。")
            return

        os.makedirs(app_dir, exist_ok=True)
        os.makedirs(APPLICATIONS_DIR, exist_ok=True)
        os.makedirs(ICONS_DIR, exist_ok=True)
        if os.path.exists(target) and os.path.abspath(app_path) != os.path.abspath(target):
            try:
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(target, backup)
                print(f"   🛡️ 已将上一版本保留为备份: {os.path.basename(backup)}")
            except OSError as e:
                print(f"   ⚠️ 备份上一版本失败: {e}")
        if os.path.abspath(app_path) != os.path.abspath(target):
            shutil.move(app_path, target)
        os.chmod(target, 0o755)

        icon = None
        if meta.get("icon_data"):
            icon = os.path.join(app_dir, f"icon.{meta['icon_ext']}")
            with open(icon, "wb") as f:
                f.write(meta["icon_data"])
            shutil.copy2(icon, os.path.join(ICONS_DIR, f"{app_id}.{meta['icon_ext']}"))
        elif prev and prev.data.get("icon_path"):
            icon = prev.data["icon_path"]

        desktop.write_desktop_entry(meta, target, icon, desktop_file)
        self.registry.register(
            app_id,
            meta["app_name"],
            meta["version"],
            meta["filename"],
            folder=folder,
            folder_path=app_dir,
            exec_path=target,
            backup_exec_path=backup,
            desktop_path=desktop_file,
            icon_path=icon,
            wm_class=meta.get("wm_class", ""),
        )
        desktop.refresh_desktop()
        print(f"   ✅ 安装就绪! 菜单快捷方式: {desktop_file}")
        desktop.notify("AppShip", f"🎉 {meta['app_name']} 已就绪 (v{meta['version']})", icon=icon or app_id)

    # ==================================================================
    # Upgrade (check + download + install)
    # ==================================================================
    def upgrade(self, checker, query=None, dry_run=False, auto_yes=False):
        if query:
            app = self.registry.find(query)
            if not app:
                print(f"❌ 未找到应用: {query}\n")
                return
            apps = [app]
        else:
            apps = self.registry.apps()
        print(f"\n🔍 正在检查 {len(apps)} 个应用的上游版本...")
        rows = checker.check_many(apps)
        pending = [(a, r) for a, r in rows if r.status == "update" or (r.force and r.download_url)]
        for app, res in rows:
            if res.status == "error":
                print(f"  ⚠️ {app.name}: {res.detail}")
        if not pending:
            print("✨ 所有应用均已是最新版本。\n")
            return
        print("\n⬇ 待升级:")
        for app, res in pending:
            print(f"  • {app.name}: {res.local} → {res.remote}  ({res.source})")
        no_url = [(a, r) for a, r in pending if not r.download_url]
        for app, _ in no_url:
            print(f"  ⚠️ {app.name}: 上游未提供可直接下载的地址，请手动下载后用 'appship install FILE' 安装")
        pending = [(a, r) for a, r in pending if r.download_url]
        if dry_run or not pending:
            if dry_run:
                print("\n🔍 [Dry-run] 不下载、不安装。")
            return
        if not confirm(f"\n❓ 下载并升级以上 {len(pending)} 个应用?", default=False, auto_yes=auto_yes):
            print("❌ 已取消。")
            return

        for app, res in pending:
            print(f"\n⬇ 下载 {app.name} {res.remote}")
            print(f"   {res.download_url}")
            try:
                path = download(
                    res.download_url, DOWNLOAD_CACHE, filename=res.filename or None, checksums=res.checksums
                )
            except AppShipError as e:
                print(f"   ❌ {e}")
                continue
            try:
                kind = res.kind or file_kind(path)
                if not kind:
                    backend = self.plugins.backend_for_file(path, {})
                    kind = backend.backend if backend else ""
                if kind == "appimage":
                    meta = appimage.extract_metadata(path)
                    remote_is_version = bool(re.match(r"^\d", res.remote or ""))
                    if remote_is_version and (
                        meta["version"] == "unknown" or compare_versions(res.remote, meta["version"]) > 0
                    ):
                        meta["version"] = res.remote
                    meta["app_name"] = app.name  # keep the registered identity across upgrades
                    meta["filename"] = res.filename or os.path.basename(path)
                    self.install_appimage(path, meta)
                else:
                    backend = self.plugins.backend(kind)
                    if backend is None:
                        raise AppShipError(f"backend 不可用: {kind}")
                    plugin = self.plugins.for_app(app)
                    backend.call(
                        "install",
                        self,
                        path,
                        plugin=plugin,
                        auto_yes=auto_yes,
                        consume=True,
                        required=True,
                    )
            except AppShipError as e:
                print(f"   ❌ 安装失败: {e}")
            except Exception as e:
                print(f"   ❌ 安装时出错: {e!r}")
        print()

    # ==================================================================
    # Rollback
    # ==================================================================
    def rollback(self, query, auto_yes=False, dry_run=False):
        app = self.registry.find(query)
        if not app:
            print(f"❌ 未找到已注册的应用: '{query}'。请先使用 'appship list' 查看。")
            return
        if app.kind != "appimage":
            backend = self.plugins.backend(app.kind)
            if backend is None:
                print(f"❌ 没有可用的 backend 处理 {app.kind} 应用。")
                return
            backend.call("rollback", self, app, auto_yes=auto_yes, dry_run=dry_run, required=True)
            return
        history = list(app.data.get("history", []))
        prev_version = next(
            (h["version"] for h in reversed(history) if h.get("version") != app.version),
            "previous",
        )
        backup = app.data.get("backup_exec_path")
        curr = app.exec_path
        if not backup or not os.path.exists(backup):
            print(f"❌ '{app.name}' 没有检测到可用的旧版本备份 (.AppImage.old)。")
            return
        print(f"⏪ 正在为 '{app.name}' 回滚到上一个版本...")
        if dry_run:
            print(f"   [Dry-run] 将交换 {curr} 与 {backup}")
            return
        if not confirm(f"❓ 确认回滚 '{app.name}'?", default=False, auto_yes=auto_yes):
            print("❌ 已取消。")
            return
        swap = curr + ".swap"
        os.replace(curr, swap)
        os.replace(backup, curr)
        os.replace(swap, backup)  # keep the newer build so the rollback can be undone
        try:
            v = appimage.extract_metadata(curr).get("version")
            if v and v != "unknown":
                prev_version = v
        except Exception:
            pass
        fields = {"source_sig": "", "exec_sig": appimage.exec_signature(curr)}
        history.append({"version": prev_version, "date": now_str(), "file": "(rollback)"})
        self.registry.update(app.id, version=prev_version, history=history, **fields)
        desktop.refresh_desktop()
        print(f"✅ 回滚成功！当前版本: {prev_version}（再执行一次 'appship rollback {app.id}' 可撤销）")

    # ==================================================================
    # Uninstall
    # ==================================================================
    def uninstall(self, query, dry_run=False, keep_data=False, auto_yes=False):
        norm = os.path.basename(re.sub(r"\.appimage$", "", query.strip(), flags=re.I).rstrip("/\\"))
        app = self.registry.find(norm)
        if not app:
            folder = os.path.join(LOCAL_DIR, norm)
            if os.path.isdir(folder):
                from .registry import App

                app_id = sanitize_id(norm)
                app = App(
                    app_id,
                    {
                        "name": norm,
                        "folder": norm,
                        "folder_path": folder,
                        "exec_path": os.path.join(folder, f"{norm}.AppImage"),
                        "desktop_path": os.path.join(APPLICATIONS_DIR, f"{app_id}.desktop"),
                    },
                )
        if not app:
            print(f"❌ 未找到匹配的应用: '{query}'。请先使用 --list 查看。")
            return
        if app.kind == "appimage":
            return self._uninstall_appimage(app, dry_run, keep_data, auto_yes)
        backend = self.plugins.backend(app.kind)
        if backend is None:
            print(f"❌ 没有可用的 backend 处理 {app.kind} 应用。")
            return
        return backend.call(
            "uninstall",
            self,
            app,
            dry_run=dry_run,
            keep_data=keep_data,
            auto_yes=auto_yes,
            required=True,
        )

    def _confirm_and_purge(self, app, sections, pids, dry_run, auto_yes, pre_remove=None):
        items = [i for _title, group in sections for i in group]
        total = sum(i["size"] for i in items)
        print(f"\n🗑️  卸载与清理确认: [{app.name}]")
        print(f"   预估将清理 {len(items)} 项资源，总计释放空间: {format_size(total)}")
        print("-" * 75)
        for title, group in sections:
            print(f"\n{title}")
            if not group:
                print("   (无)")
            for i in group:
                label = f"[{i['category']}] " if i.get("category") else ""
                print(f"   • {label}[{format_size(i['size'])}] {i['path']}")
        print("\n" + "-" * 75)
        if dry_run:
            print("🔍 [Dry-run] 演练模式结束，未做任何实际删除。")
            return False
        if not confirm("❓ 是否确认彻底清理以上所有项目?", default=False, auto_yes=auto_yes):
            print("❌ 已取消卸载操作。")
            return False
        if pids:
            print(f"   🔄 正在关闭应用进程 (PID: {', '.join(pids)}) ...")
            desktop.terminate(pids)
        if pre_remove and not pre_remove():
            return False
        for i in items:
            p = i["path"]
            if not os.path.lexists(p):
                continue
            try:
                shutil.rmtree(p) if i.get("is_dir") and not os.path.islink(p) else os.remove(p)
                print(f"   ✅ 已清除: {p}")
            except OSError as e:
                print(f"   ⚠️ 清除失败: {p} ({e})")
        self.registry.unregister(app.id)
        desktop.refresh_desktop()
        print(f"\n🎉 '{app.name}' 已彻底卸载！共释放 {format_size(total)} 磁盘空间。")
        desktop.notify("AppShip", f"🗑️ {app.name} 已卸载 (释放 {format_size(total)})")
        return True

    def _data_files(self, app, keep_data, *identities):
        if keep_data:
            return []
        plugin = self.plugins.for_app(app)
        extra = (plugin._call("data_dirs", app) if plugin else None) or []
        return desktop.find_footprints(*identities, extra_dirs=extra)

    def _uninstall_appimage(self, app, dry_run, keep_data, auto_yes):
        d = app.data
        folder = d.get("folder", app.name)
        folder_path = d.get("folder_path", os.path.join(LOCAL_DIR, folder))
        desktop_path = d.get("desktop_path", os.path.join(APPLICATIONS_DIR, f"{app.id}.desktop"))
        pids = desktop.running_pids(app.exec_path, exe_prefixes=(folder_path,))
        if pids:
            print(f"⚠️ 注意: '{app.name}' 似乎正在运行 (PID: {', '.join(pids)})。")

        def entry(p, desc=""):
            return {"path": p, "size": path_size(p), "is_dir": os.path.isdir(p), "category": desc}

        app_files = []
        if folder_path and os.path.isdir(folder_path):
            app_files.append(entry(folder_path))
        else:
            app_files += [entry(p) for p in (app.exec_path, d.get("backup_exec_path")) if p and os.path.exists(p)]
        for lp in desktop.find_loose_installers(app.id, folder, app.name):
            if not (folder_path and os.path.commonpath([folder_path, lp]) == folder_path):
                app_files.append(entry(lp, "散落的执行文件"))

        sys_files, seen = [], set()
        for p in [desktop_path, os.path.join(APPLICATIONS_DIR, f"{app.id}.desktop")]:
            if p and os.path.exists(p) and p not in seen:
                seen.add(p)
                sys_files.append(entry(p))
        for pat in (f"{app.id}.*", f"hicolor/*/apps/{app.id}.*", f"hicolor/*/apps/{folder}.*"):
            for ip in glob.glob(os.path.join(ICONS_DIR, pat)):
                if ip not in seen:
                    seen.add(ip)
                    sys_files.append(entry(ip))

        data_files = self._data_files(app, keep_data, app.name, folder, app.id, d.get("wm_class", ""))
        sections = [
            ("📦 1. 执行程序与应用目录:", app_files),
            ("🖥️  2. 桌面快捷方式与图标:", sys_files),
            ("🗄️  3. 应用工作数据、配置与缓存:" + (" (已指定 --keep-data，跳过)" if keep_data else ""), data_files),
        ]
        self._confirm_and_purge(app, sections, pids, dry_run, auto_yes)

    # ==================================================================
    # Adopt an already-installed pacman package
    # ==================================================================
    def adopt_pkg(self, pkgname, plugin_name=""):
        for backend in self.plugins.backends():
            if callable(getattr(backend.module, "adopt", None)):
                return backend.call("adopt", self, pkgname, plugin_name, required=True)
        raise AppShipError("没有可用的 backend 支持 adopt")
