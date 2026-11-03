from appship.registry import AppRegistry, sanitize_folder_name, sanitize_id


def test_registry_round_trip(tmp_path):
    db = tmp_path / "apps.json"
    app_dir = tmp_path / "Foo"
    app_dir.mkdir()
    exec_path = app_dir / "Foo.AppImage"
    exec_path.write_bytes(b"fake")
    backup = app_dir / "Foo.AppImage.old"
    desktop = tmp_path / "foo.desktop"
    registry = AppRegistry(db_path=str(db))
    registry.register(
        "foo",
        "Foo",
        "1.0.0",
        "Foo.AppImage",
        folder="Foo",
        folder_path=str(app_dir),
        exec_path=str(exec_path),
        backup_exec_path=str(backup),
        desktop_path=str(desktop),
        icon_path="",
        wm_class="Foo",
    )
    app = registry.get("foo")
    assert app is not None
    assert app.name == "Foo"
    assert app.version == "1.0.0"
    assert app.installed is True
    assert registry.find("Foo").id == "foo"

    reloaded = AppRegistry(db_path=str(db))
    assert reloaded.get("foo").version == "1.0.0"


def test_sanitize_id_strips_channel_words():
    assert sanitize_id("Foo (Beta)") == "foo"
    assert sanitize_id("Bar - Insiders") == "bar"
    assert sanitize_folder_name("OpenChamber") == "openchamber"
