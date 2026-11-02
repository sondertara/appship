import glob
import os
from types import SimpleNamespace

import appman.migrate as migrate_mod


class FakeRegistry:
    def __init__(self, apps, db_path):
        self._apps = apps
        self.db_path = db_path
        self.saved = None

    def apps(self):
        return self._apps

    def save(self):
        self.saved = self.db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as fh:
            fh.write("{}")


def _setup(tmp_path, monkeypatch):
    legacy = tmp_path / "local"
    app_dir = legacy / "Foo"
    app_dir.mkdir(parents=True)
    exec_path = app_dir / "Foo.AppImage"
    exec_path.write_bytes(b"fake")
    backup = app_dir / "Foo.AppImage.old"
    backup.write_bytes(b"old")
    icon = app_dir / "icon.png"
    icon.write_bytes(b"icon")
    applications = tmp_path / "applications"
    applications.mkdir()
    desktop = applications / "foo.desktop"
    desktop.write_text(f'[Desktop Entry]\nExec="{exec_path}" %U\nIcon={icon}\n')
    plugins = legacy / "appman.d" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "local.py").write_text("NAME = 'local'\n")

    legacy_db = legacy / ".apps.json"
    legacy_db.write_text("{}")
    target = tmp_path / "share" / "appman"
    target_apps = target / "apps"
    target_db = target / "apps.json"
    target_plugins = target / "plugins"
    target_deb = target / "debpkgs"

    data = {
        "folder_path": str(app_dir),
        "exec_path": str(exec_path),
        "backup_exec_path": str(backup),
        "icon_path": str(icon),
        "desktop_path": str(desktop),
        "folder": "Foo",
    }
    app = SimpleNamespace(id="foo", kind="appimage", data=data)
    registry = FakeRegistry([app], str(legacy_db))

    monkeypatch.setattr(migrate_mod, "LEGACY_LOCAL_DIR", str(legacy))
    monkeypatch.setattr(migrate_mod, "LEGACY_DB_PATH", str(legacy_db))
    monkeypatch.setattr(migrate_mod, "LEGACY_PLUGINS_DIR", str(plugins))
    monkeypatch.setattr(migrate_mod, "LEGACY_DEB_DIR", str(legacy / "debpkgs"))
    monkeypatch.setattr(migrate_mod, "TARGET_APPS_DIR", str(target_apps))
    monkeypatch.setattr(migrate_mod, "TARGET_REGISTRY_PATH", str(target_db))
    monkeypatch.setattr(migrate_mod, "TARGET_PLUGINS_DIR", str(target_plugins))
    monkeypatch.setattr(migrate_mod, "TARGET_DEB_DIR", str(target_deb))
    monkeypatch.setattr(migrate_mod, "APPLICATIONS_DIR", str(applications))
    monkeypatch.setattr(migrate_mod.desktop, "refresh_desktop", lambda: None)
    return registry, legacy_db, app_dir, target_apps, target_db, target_plugins, desktop, data


def test_migrate_dry_run_does_not_touch_files(tmp_path, monkeypatch):
    (registry, legacy_db, app_dir, target_apps, target_db, _target_plugins, _desktop, data) = _setup(
        tmp_path, monkeypatch
    )
    assert migrate_mod.migrate(registry, dry_run=True, auto_yes=True) == 0
    assert app_dir.is_dir()
    assert not target_apps.exists()
    assert legacy_db.exists()
    assert registry.saved is None
    assert data["folder_path"] == str(app_dir)


def test_migrate_apply_moves_and_rewrites(tmp_path, monkeypatch):
    (registry, legacy_db, app_dir, target_apps, target_db, target_plugins, desktop, data) = _setup(
        tmp_path, monkeypatch
    )
    assert migrate_mod.migrate(registry, dry_run=False, auto_yes=True) == 0
    new_dir = target_apps / "Foo"
    assert new_dir.is_dir()
    assert not app_dir.exists()
    assert data["folder_path"] == str(new_dir)
    assert data["exec_path"] == str(new_dir / "Foo.AppImage")
    assert data["backup_exec_path"] == str(new_dir / "Foo.AppImage.old")
    assert data["icon_path"] == str(new_dir / "icon.png")
    text = desktop.read_text()
    assert str(new_dir / "Foo.AppImage") in text
    assert str(new_dir / "icon.png") in text
    assert str(app_dir) not in text
    assert registry.saved == str(target_db)
    assert (target_plugins / "local.py").is_file()
    assert not legacy_db.exists()
    assert glob.glob(str(legacy_db) + ".pre-migrate-*")
