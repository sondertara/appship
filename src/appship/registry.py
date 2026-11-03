"""Central application registry (XDG data dir, or legacy ~/local/.apps.json).

Every entry keeps the original AppImage-era fields; newer fields are optional:
  kind            'appimage' (default when absent) | 'deb'
  pacman_pkg      deb only: local pacman package name
  deb_dir         deb only: appship deb build directory
  backup_pkg      deb only: previously built package file, used by --rollback
   update_source   cached update source, e.g. {"type": "github", "repo": "o/r", "via": "app-update.yml"}
  source_sig      "<size>:<mtime>" of the executable the cached source was discovered from
  exec_sig        "<size>:<mtime>" of the executable when its version was last synced
  source_pinned   true when set by hand with --source (never re-discovered)
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime

from . import appimage
from .config import APPLICATIONS_DIR, LOCAL_DIR, VERSIONS_DB_PATH
from .util import pad


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitize_id(name):
    s = re.sub(r"[^\w\-\.]+", "-", strip_channel_qualifiers(name).strip())
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s or "app"


def sanitize_folder_name(name):
    return sanitize_id(name)


_CHANNEL_WORDS = r"(?:alpha|beta|rc\d*|dev|nightly|preview|canary|insiders|insider|stable)"


def strip_channel_qualifiers(name):
    """'Foo (Beta)' / 'Foo - Insiders' -> 'Foo'."""
    if not name:
        return name
    s = re.sub(r"[\(\[\{]\s*" + _CHANNEL_WORDS + r"\s*[\)\]\}]", "", name, flags=re.I).strip()
    cand = re.sub(r"(?:[\s\-_]+)" + _CHANNEL_WORDS + r"\b\s*$", "", s, flags=re.I).strip()
    if re.search(r"[a-zA-Z0-9一-龥]", cand):
        s = cand
    return s or name


class App:
    """Thin view over one registry entry, handed to plugins and checkers."""

    def __init__(self, app_id, data):
        self.id = app_id
        self.data = data

    @property
    def kind(self):
        return self.data.get("kind") or "appimage"

    @property
    def name(self):
        return self.data.get("name") or self.id

    @property
    def version(self):
        return self.data.get("version") or "unknown"

    @property
    def exec_path(self):
        return self.data.get("exec_path") or ""

    @property
    def pacman_pkg(self):
        return self.data.get("pacman_pkg") or ""

    @property
    def installed(self):
        if self.kind != "appimage":
            return bool(self.data.get("pkg_installed", True))
        return bool(self.exec_path) and os.path.isfile(self.exec_path)

    def __repr__(self):
        return f"App({self.id!r}, kind={self.kind!r}, version={self.version!r})"


class AppRegistry:
    def __init__(self, db_path=VERSIONS_DB_PATH):
        self.db_path = db_path
        self.db = self._load()
        self._rebase_paths()
        self._sync_appimage_versions()

    # ------------------------------------------------------------------ persistence
    def _load(self):
        if not os.path.exists(self.db_path):
            return {}
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            backup = f"{self.db_path}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                shutil.copy2(self.db_path, backup)
                print(f"⚠️ 读取 .apps.json 失败: {e}。已将损坏文件备份至: {backup}", file=sys.stderr)
            except OSError:
                pass
            return {}

    def save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        tmp = self.db_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.db, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, self.db_path)

    def _rebase_paths(self):
        """Adapt AppImage paths if the database was copied from another machine or account."""
        modified = False
        for app_id, data in self.db.items():
            if data.get("kind") == "deb":
                continue
            folder = data.get("folder") or app_id
            curr_folder = os.path.join(LOCAL_DIR, folder)
            if not os.path.isdir(curr_folder):
                continue
            wanted = {
                "folder_path": curr_folder,
                "backup_exec_path": os.path.join(curr_folder, f"{folder}.AppImage.old"),
                "desktop_path": os.path.join(APPLICATIONS_DIR, f"{app_id}.desktop"),
            }
            curr_exec = os.path.join(curr_folder, f"{folder}.AppImage")
            if os.path.exists(curr_exec):
                wanted["exec_path"] = curr_exec
            for ext in ("png", "svg"):
                icon = os.path.join(curr_folder, f"icon.{ext}")
                if os.path.exists(icon):
                    wanted["icon_path"] = icon
                    break
            for key, value in wanted.items():
                if data.get(key) != value:
                    data[key] = value
                    modified = True
        if modified:
            self.save()

    def _sync_appimage_versions(self):
        """Detect AppImages replaced in-place (e.g. via in-app electron-updater) and sync their version."""
        if not appimage.have_7z():
            return
        modified = False
        for app_id, data in self.db.items():
            if data.get("kind") == "deb":
                continue
            exec_path = data.get("exec_path") or ""
            if not exec_path or not os.path.isfile(exec_path):
                continue
            sig = appimage.exec_signature(exec_path)
            if not sig or data.get("exec_sig") == sig:
                continue
            try:
                ts = datetime.fromtimestamp(os.stat(exec_path).st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                ts = now_str()
            try:
                meta = appimage.extract_metadata(exec_path)
            except Exception:
                continue
            new_ver = meta.get("version", "unknown")
            old_ver = data.get("version", "unknown")
            if new_ver != "unknown" and new_ver != old_ver:
                data["version"] = new_ver
                data["installed_at"] = ts
                history = data.setdefault("history", [])
                if old_ver == "unknown" and history and history[-1].get("version") == "unknown":
                    history[-1]["version"] = new_ver
                else:
                    history.append({"version": new_ver, "date": ts, "file": "(应用内更新)"})
                print(f"🔄 检测到 [{data.get('name', app_id)}] 已在应用内更新: {old_ver} -> {new_ver}", file=sys.stderr)
            if not data.get("wm_class") and meta.get("wm_class"):
                data["wm_class"] = meta["wm_class"]
            data["exec_sig"] = sig
            modified = True
        if modified:
            self.save()

    # ------------------------------------------------------------------ lookup
    def apps(self):
        return [App(k, v) for k, v in sorted(self.db.items(), key=lambda kv: kv[0].lower())]

    def get(self, app_id):
        return App(app_id, self.db[app_id]) if app_id in self.db else None

    def find(self, query, quiet=False):
        """Look an app up by ID, display name, folder, pacman package or a unique fuzzy match."""
        if not query:
            return None
        q = str(query).strip().lower()
        q_norm = re.sub(r"[\s\-_]+", "", q)

        def keys(k, v):
            return [k, v.get("name", "").lower(), v.get("folder", "").lower(), (v.get("pacman_pkg") or "").lower()]

        for k, v in self.db.items():
            if q in keys(k, v):
                return App(k, v)
        for k, v in self.db.items():
            if q_norm and q_norm in [re.sub(r"[\s\-_]+", "", x) for x in keys(k, v) if x]:
                return App(k, v)
        matches = [(k, v) for k, v in self.db.items() if any(q in x for x in keys(k, v) if x)]
        if len(matches) == 1:
            return App(*matches[0])
        if len(matches) > 1 and not quiet:
            names = ", ".join(f"{v.get('name', k)} [{k}]" for k, v in matches)
            print(f"❌ 找到多个匹配的应用: {names}，请输入更具体的名称或应用 ID。", file=sys.stderr)
        return None

    def find_by_pkg(self, pkg):
        for k, v in self.db.items():
            if (v.get("pacman_pkg") or k) == pkg:
                return App(k, v)
        return None

    # ------------------------------------------------------------------ mutation
    def register(self, app_id, name, version, orig_filename, **fields):
        """Create or replace an entry, appending to its version history."""
        history = self.db.get(app_id, {}).get("history", [])
        history.append({"version": version, "date": now_str(), "file": orig_filename})
        keep = {
            k: v for k, v in self.db.get(app_id, {}).items() if k in ("update_source", "source_sig", "source_pinned")
        }
        entry = {"name": name, "version": version, **fields, "installed_at": now_str(), "history": history}
        if entry.get("kind") in (None, "", "appimage"):
            entry.pop("kind", None)
            icon = entry.get("icon_path")
            entry["icon_path"] = icon if (icon and os.path.exists(icon)) else ""
            entry["exec_sig"] = appimage.exec_signature(entry.get("exec_path", ""))
        entry.update({k: v for k, v in keep.items() if k not in entry})
        self.db[app_id] = entry
        self.save()
        return App(app_id, entry)

    def update(self, app_id, **fields):
        if app_id in self.db:
            self.db[app_id].update(fields)
            self.save()

    def unregister(self, app_id):
        if self.db.pop(app_id, None) is not None:
            self.save()

    # ------------------------------------------------------------------ views
    def list_apps(self):
        print(f"\n📂 已在 {self.db_path} 统一注册管理的应用:")
        if not self.db:
            print("  (暂无通过该脚本管理的应用)\n")
            return
        print(
            f"  {pad('应用 ID (App ID)', 26)} {pad('应用名称', 24)} {pad('类型', 9)} "
            f"{pad('当前版本', 18)} {pad('可否回滚', 10)} 安装/更新时间"
        )
        print("  " + "-" * 112)
        has_unknown = False
        for app in self.apps():
            d = app.data
            ver = app.version
            has_unknown |= ver == "unknown"
            if app.kind == "appimage":
                kind = "AppImage"
                backup = d.get("backup_exec_path")
            else:
                kind = app.kind
                if not d.get("pkg_installed", True):
                    ver = f"{ver} (未安装)"
                backup = d.get("backup_pkg") or d.get("backup_exec_path")
            rollback = "是" if (backup and os.path.exists(backup)) else "否"
            print(
                f"  {pad(app.id, 26)} {pad(app.name, 24)} {pad(kind, 9)} {pad(ver, 18)} "
                f"{pad(rollback, 10)} {d.get('installed_at', '')}"
            )
        print("\n  💡 操作命令推荐使用无空格的应用 ID (例如: appship uninstall <App ID>)")
        print("  💡 'appship check' 检查上游新版本，'appship upgrade' 自动下载并升级。")
        if has_unknown:
            print("  💡 部分应用版本为 unknown，可运行 'appship repair' 自动嗅探修复。")
        print()

    def show_history(self, query=None):
        if query:
            app = self.find(query)
            if not app:
                print(f"❌ 未找到应用: {query}\n")
                return
            targets = [app]
        else:
            targets = self.apps()
        for app in targets:
            print(f"\n📜 [{app.name}] 版本更新历史:")
            history = app.data.get("history", [])
            if not history:
                print("  (暂无历史记录)")
            for idx, h in enumerate(history, 1):
                orig = f" (源文件: {h['file']})" if h.get("file") else ""
                print(f"  {idx}. 版本: {h['version']:<16} 时间: {h['date']}{orig}")
        print()
