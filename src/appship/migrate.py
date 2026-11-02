"""Move a legacy ``~/local`` installation to the XDG layout.

The registry, app directories, deb build dirs, desktop Exec/Icon paths, and
user plugins are migrated. Unrelated files in ``~/local`` are left untouched.
``appship migrate`` is a dry-run by default; ``--apply`` performs the move.
"""

import os
import shutil
from datetime import datetime

from . import desktop
from .config import (
    APPLICATIONS_DIR,
    LEGACY_DB_PATH,
    LEGACY_DEB_DIR,
    LEGACY_LOCAL_DIR,
    LEGACY_PLUGINS_DIR,
    TARGET_APPS_DIR,
    TARGET_DEB_DIR,
    TARGET_PLUGINS_DIR,
    TARGET_REGISTRY_PATH,
)
from .util import confirm


def _stamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _prefix(value, old, new):
    if not value or not old:
        return value
    old = old.rstrip(os.sep)
    if value == old:
        return new
    if value.startswith(old + os.sep):
        return new.rstrip(os.sep) + value[len(old) :]
    return value


def _desktop_path(app):
    data = app.data
    return data.get("desktop_path") or os.path.join(APPLICATIONS_DIR, f"{app.id}.desktop")


def _rewrite_desktop(path, replacements, dry_run):
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    new_text = text
    for old, new in replacements:
        if old and new and old != new:
            new_text = new_text.replace(old, new)
    if new_text == text:
        return False
    if dry_run:
        return True
    backup = f"{path}.appship-migrate-{_stamp()}"
    try:
        shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except OSError as exc:
        print(f"  ⚠️ 无法重写桌面文件 {path}: {exc}")
        return False
    return True


def _collect(registry):
    actions = []
    for app in registry.apps():
        data = app.data
        if app.kind == "deb":
            pkg = app.pacman_pkg or app.id
            old = data.get("deb_dir") or os.path.join(LEGACY_DEB_DIR, pkg)
            new = os.path.join(TARGET_DEB_DIR, os.path.basename(old.rstrip(os.sep)))
            if os.path.abspath(old) == os.path.abspath(new):
                continue
            if os.path.isdir(old):
                actions.append(
                    {
                        "kind": "deb_dir",
                        "app": app,
                        "old": old,
                        "new": new,
                        "old_pkg": data.get("package_file", ""),
                        "new_pkg": _prefix(data.get("package_file", ""), old, new),
                        "old_backup": data.get("backup_pkg", ""),
                        "new_backup": _prefix(data.get("backup_pkg", ""), old, new),
                    }
                )
            elif os.path.isdir(new):
                actions.append(
                    {
                        "kind": "deb_path_only",
                        "app": app,
                        "old": old,
                        "new": new,
                        "old_pkg": data.get("package_file", ""),
                        "new_pkg": _prefix(data.get("package_file", ""), old, new),
                        "old_backup": data.get("backup_pkg", ""),
                        "new_backup": _prefix(data.get("backup_pkg", ""), old, new),
                    }
                )
            continue

        old = data.get("folder_path") or os.path.join(LEGACY_LOCAL_DIR, data.get("folder") or app.id)
        new = os.path.join(TARGET_APPS_DIR, os.path.basename(old.rstrip(os.sep)))
        if os.path.abspath(old) == os.path.abspath(new):
            continue
        old_exec = data.get("exec_path", "")
        old_icon = data.get("icon_path", "")
        action = {
            "kind": "app_dir" if os.path.isdir(old) else ("app_path_only" if os.path.isdir(new) else ""),
            "app": app,
            "old": old,
            "new": new,
            "old_exec": old_exec,
            "new_exec": _prefix(old_exec, old, new),
            "old_icon": old_icon,
            "new_icon": _prefix(old_icon, old, new),
            "desktop_path": _desktop_path(app),
        }
        if action["kind"]:
            actions.append(action)

    if os.path.isdir(LEGACY_PLUGINS_DIR):
        for fname in sorted(os.listdir(LEGACY_PLUGINS_DIR)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            old = os.path.join(LEGACY_PLUGINS_DIR, fname)
            new = os.path.join(TARGET_PLUGINS_DIR, fname)
            actions.append({"kind": "plugin", "old": old, "new": new})
    return actions


def _print_action(action):
    kind = action["kind"]
    old, new = action["old"], action["new"]
    if kind == "plugin":
        print(f"  🧩 插件: {old} -> {new}")
        return
    label = "deb 构建目录" if kind.startswith("deb") else "应用目录"
    print(f"  📁 {label}: {old} -> {new}")
    if kind.startswith("app"):
        if action["old_exec"] and action["old_exec"] != action["new_exec"]:
            print(f"      Exec: {action['old_exec']} -> {action['new_exec']}")
        if action["old_icon"] and action["old_icon"] != action["new_icon"]:
            print(f"      Icon: {action['old_icon']} -> {action['new_icon']}")
        if action["desktop_path"] and os.path.isfile(action["desktop_path"]):
            print(f"      桌面项: {action['desktop_path']}")


def _rewrite_for_action(action, dry_run):
    if action["kind"] not in ("app_dir", "app_path_only"):
        return False
    return _rewrite_desktop(
        action["desktop_path"],
        [
            (action["old_exec"], action["new_exec"]),
            (action["old_icon"], action["new_icon"]),
            (action["old"], action["new"]),
        ],
        dry_run,
    )


def _apply_data(action):
    app = action["app"]
    data = app.data
    kind = action["kind"]
    if kind in ("app_dir", "app_path_only"):
        data["folder_path"] = action["new"]
        data["exec_path"] = action["new_exec"]
        data["backup_exec_path"] = _prefix(data.get("backup_exec_path", ""), action["old"], action["new"])
        data["icon_path"] = action["new_icon"]
    elif kind in ("deb_dir", "deb_path_only"):
        data["deb_dir"] = action["new"]
        data["package_file"] = action["new_pkg"]
        data["backup_pkg"] = action["new_backup"]


def migrate(registry, dry_run=True, auto_yes=False):
    if os.path.exists(TARGET_REGISTRY_PATH):
        print(f"✅ 已经是 XDG 布局: {TARGET_REGISTRY_PATH}")
        return 0
    if not os.path.exists(LEGACY_DB_PATH):
        print("✅ 没有发现旧的 ~/local/.apps.json，无需迁移。")
        return 0

    actions = _collect(registry)
    print(f"\n📦 迁移计划 ({'预览' if dry_run else '执行'}):")
    print(f"  旧注册表: {LEGACY_DB_PATH}")
    print(f"  新注册表: {TARGET_REGISTRY_PATH}")
    print(f"  新应用目录: {TARGET_APPS_DIR}")

    conflicts = []
    for action in actions:
        if action["kind"] == "plugin" and os.path.exists(action["new"]):
            print(f"  ⏭️ 插件已存在，跳过: {action['new']}")
            action["skip"] = True
            continue
        if action["kind"] in ("app_dir", "deb_dir") and os.path.exists(action["new"]):
            print(f"  ⚠️ 目标已存在，跳过: {action['new']}")
            conflicts.append(action)
            action["skip"] = True
            continue
        _print_action(action)

    if conflicts:
        print(f"\n⚠️ {len(conflicts)} 项目标已存在，迁移时会跳过；请先处理冲突。")

    if dry_run:
        print("\n🔍 [Dry-run] 未写入任何文件。运行 'appship migrate --apply' 执行。\n")
        return 0
    if not confirm("\n❓ 执行以上迁移?", default=False, auto_yes=auto_yes):
        print("❌ 已取消。")
        return 0

    os.makedirs(TARGET_APPS_DIR, exist_ok=True)
    os.makedirs(TARGET_DEB_DIR, exist_ok=True)
    os.makedirs(TARGET_PLUGINS_DIR, exist_ok=True)

    moved = 0
    failed = 0
    for action in actions:
        if action.get("skip"):
            continue
        kind = action["kind"]
        old, new = action["old"], action["new"]
        try:
            if kind == "plugin":
                with open(old, "r", encoding="utf-8") as fh:
                    plugin_text = fh.read()
                plugin_text = plugin_text.replace("from appman.api", "from appship.api")
                plugin_text = plugin_text.replace("from appman.", "from appship.")
                plugin_text = plugin_text.replace("import appman", "import appship")
                plugin_text = plugin_text.replace("AppManError", "AppShipError")
                with open(new, "w", encoding="utf-8") as fh:
                    fh.write(plugin_text)
                shutil.copystat(old, new)
            elif kind in ("app_dir", "deb_dir"):
                os.makedirs(os.path.dirname(new), exist_ok=True)
                shutil.move(old, new)
            elif kind not in ("app_path_only", "deb_path_only"):
                continue
            if kind != "plugin":
                _apply_data(action)
                _rewrite_for_action(action, dry_run=False)
            moved += 1
        except OSError as exc:
            failed += 1
            print(f"  ❌ 迁移失败 {old}: {exc}")

    backup = f"{LEGACY_DB_PATH}.pre-migrate-{_stamp()}"
    try:
        shutil.copy2(LEGACY_DB_PATH, backup)
    except OSError as exc:
        print(f"⚠️ 备份旧注册表失败，已停止: {exc}")
        return 1

    registry.db_path = TARGET_REGISTRY_PATH
    os.makedirs(os.path.dirname(TARGET_REGISTRY_PATH), exist_ok=True)
    try:
        registry.save()
    except OSError as exc:
        print(f"❌ 写入新注册表失败: {exc}")
        return 1
    try:
        os.replace(LEGACY_DB_PATH, backup)
    except OSError as exc:
        print(f"⚠️ 旧注册表未移除（新注册表已写入）: {exc}")

    desktop.refresh_desktop()
    print(f"\n✅ 迁移完成，已处理 {moved} 项，失败 {failed} 项。旧注册表备份: {backup}")
    print("   ~/local 中未注册的文件保持原样；旧的 appman.d 代码可手动删除。\n")
    return 0 if not failed else 1
