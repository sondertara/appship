"""--repair: re-scan installed AppImages, normalise IDs/folders, fill unknown versions, rebuild shortcuts."""

import glob
import os
import shutil

from . import appimage, desktop
from .config import APPLICATIONS_DIR, ICONS_DIR, LOCAL_DIR
from .registry import now_str, sanitize_folder_name, sanitize_id
from .util import clean_version


def _fallback_meta(data, app_id):
    return {
        "app_name": data.get("name", app_id),
        "comment": "",
        "categories": "Utility;",
        "wm_class": data.get("wm_class", ""),
        "mime_type": "",
    }


def repair(registry, query=None):
    print(f"\n🔧 正在扫描并修复 {LOCAL_DIR} 中已安装应用的版本与元数据...")
    if query:
        app = registry.find(query)
        if not app:
            print(f"❌ 未找到应用: {query}\n")
            return
        targets = [(app.id, app.data)]
    else:
        targets = list(registry.db.items())
    updated = 0
    for app_id, data in targets:
        if data.get("kind") == "deb":
            continue
        exec_path = data.get("exec_path")
        if not exec_path or not os.path.exists(exec_path):
            folder_path = data.get("folder_path")
            if folder_path and os.path.isdir(folder_path):
                cands = [f for f in glob.glob(os.path.join(folder_path, "*.AppImage"))]
                if cands:
                    exec_path = data["exec_path"] = cands[0]
        if not exec_path or not os.path.exists(exec_path):
            continue

        changes = []
        name = data.get("name", app_id)
        canonical_id = sanitize_id(name)
        canonical_folder = sanitize_folder_name(name)
        cur_folder = data.get("folder", canonical_folder)
        cur_folder_path = data.get("folder_path") or os.path.join(LOCAL_DIR, cur_folder)

        if cur_folder != canonical_folder:
            new_path = os.path.join(LOCAL_DIR, canonical_folder)
            if os.path.exists(cur_folder_path) and not os.path.exists(new_path):
                try:
                    shutil.move(cur_folder_path, new_path)
                    for old, new in (
                        (f"{cur_folder}.AppImage", f"{canonical_folder}.AppImage"),
                        (f"{cur_folder}.AppImage.old", f"{canonical_folder}.AppImage.old"),
                    ):
                        if os.path.exists(os.path.join(new_path, old)):
                            shutil.move(os.path.join(new_path, old), os.path.join(new_path, new))
                    data.update(
                        folder=canonical_folder,
                        folder_path=new_path,
                        exec_path=os.path.join(new_path, f"{canonical_folder}.AppImage"),
                        backup_exec_path=os.path.join(new_path, f"{canonical_folder}.AppImage.old"),
                    )
                    if data.get("icon_path", "").startswith(cur_folder_path):
                        data["icon_path"] = data["icon_path"].replace(cur_folder_path, new_path, 1)
                    exec_path = data["exec_path"]
                    changes.append(f"目录规范化: {cur_folder} -> {canonical_folder}")
                except OSError as e:
                    print(f"  ⚠️ 迁移目录失败: {e}")

        target_id = app_id
        if app_id != canonical_id and canonical_id not in registry.db:
            old_desktop = data.get("desktop_path") or os.path.join(APPLICATIONS_DIR, f"{app_id}.desktop")
            if os.path.exists(old_desktop):
                try:
                    os.remove(old_desktop)
                except OSError:
                    pass
            data["desktop_path"] = os.path.join(APPLICATIONS_DIR, f"{canonical_id}.desktop")
            old_icon = os.path.join(ICONS_DIR, f"{app_id}.png")
            if os.path.exists(old_icon):
                shutil.move(old_icon, os.path.join(ICONS_DIR, f"{canonical_id}.png"))
            registry.db[canonical_id] = registry.db.pop(app_id)
            target_id = canonical_id
            changes.append(f"应用 ID 规范化: {app_id} -> {canonical_id}")

        desk = data.get("desktop_path") or os.path.join(APPLICATIONS_DIR, f"{target_id}.desktop")
        if not os.path.exists(desk) or any("规范化" in c for c in changes):
            try:
                meta = appimage.extract_metadata(exec_path)
            except Exception:
                meta = _fallback_meta(data, target_id)
            meta["app_name"] = name
            desktop.write_desktop_entry(meta, exec_path, data.get("icon_path"), desk)
            data["desktop_path"] = desk
            icon = data.get("icon_path")
            if icon and os.path.exists(icon):
                sys_icon = os.path.join(ICONS_DIR, f"{target_id}.{icon.rsplit('.', 1)[-1]}")
                if not os.path.exists(sys_icon):
                    shutil.copy2(icon, sys_icon)
            changes.append(f"重建桌面快捷方式: {os.path.basename(desk)}")

        ver = data.get("version", "unknown")
        try:
            meta = appimage.extract_metadata(exec_path)
        except Exception:
            meta = {}
        extracted_ver = meta.get("version", "unknown")
        if extracted_ver != "unknown" and extracted_ver != ver:
            data["version"] = extracted_ver
            history = data.setdefault("history", [])
            if ver == "unknown":
                for h in history:
                    if h.get("version") == "unknown":
                        h["version"] = extracted_ver
                changes.append(f"版本: unknown -> {extracted_ver}")
            else:
                history.append({"version": extracted_ver, "date": now_str(), "file": "(应用内更新)"})
                changes.append(f"版本同步: {ver} -> {extracted_ver}")
        elif ver != "unknown":
            cleaned = clean_version(ver)
            if cleaned != ver:
                data["version"] = cleaned
                for h in data.get("history", []):
                    if h.get("version") == ver:
                        h["version"] = cleaned
                changes.append(f"版本规范化: {ver} -> {cleaned}")
        if not data.get("wm_class") and meta.get("wm_class"):
            data["wm_class"] = meta["wm_class"]
            changes.append(f"WMClass: {meta['wm_class']}")
        sig = appimage.exec_signature(exec_path)
        if sig and data.get("exec_sig") != sig:
            data["exec_sig"] = sig

        if changes:
            data.pop("source_sig", None)  # force update-source re-discovery
            print(f"  ✨ [{name}] {', '.join(changes)}")
            updated += 1

    if updated:
        registry.save()
        desktop.refresh_desktop()
        print(f"✅ 修复完成！共修复 {updated} 款应用。\n")
    else:
        print("✅ 所有已安装应用的元数据均完整，无需修复。\n")
