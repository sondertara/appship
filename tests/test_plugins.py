from appship.plugins import PluginManager


def test_builtin_plugins_load(tmp_path):
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    names = {p.name for p in manager.plugins}
    assert {"antigravity", "codebuddy", "wechat", "deb"} <= names
    antigravity = manager.get("antigravity")
    assert antigravity is not None
    assert antigravity.source == "builtin"
    assert "check" in antigravity.hooks()
    deb = manager.get("deb")
    assert deb is not None
    assert deb.backend == "deb"
    codebuddy = manager.get("codebuddy")
    assert codebuddy is not None
    assert codebuddy.requires_backend == "deb"
    assert "deb_package_extra" in codebuddy.hooks()
    assert manager.is_available(codebuddy) == manager.is_available(deb)


def test_deb_backend_unavailable_disables_codebuddy(tmp_path, monkeypatch):
    from appship import debpkg

    monkeypatch.setattr(debpkg, "_missing_tools", lambda: ["pacman"])
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    deb = manager.get("deb")
    assert deb is not None
    assert manager.is_available(deb) is False
    assert manager.backend("deb") is None
    assert manager.is_available(manager.get("codebuddy")) is False


def test_no_plugins_mode(tmp_path):
    manager = PluginManager(plugins_dirs=[str(tmp_path)], load_plugins=False)
    assert manager.plugins == []
    assert manager.backends() == []


def test_user_plugin_overrides_builtin(tmp_path):
    (tmp_path / "antigravity.py").write_text(
        "NAME = 'antigravity'\nDESCRIPTION = 'local override'\ndef match(app):\n    return False\n"
    )
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    matches = [p for p in manager.plugins if p.name == "antigravity"]
    assert len(matches) == 1
    assert matches[0].source == "user"
    assert matches[0].description == "local override"


def test_legacy_plugin_import_alias(tmp_path):
    (tmp_path / "legacy.py").write_text(
        "from appman.api import AppManError\n"
        "NAME = 'legacy'\n"
        "DESCRIPTION = 'legacy import compatibility'\n"
        "def match(app):\n"
        "    return False\n"
    )
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    assert manager.get("legacy") is not None
    assert not manager.errors


def test_codebuddy_package_extra_preserves_setuid(tmp_path):
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    plugin = manager.get("codebuddy")
    extra = plugin.call("deb_package_extra", {})
    assert "chmod 4755" in extra
    assert "chrome-sandbox" in extra


def test_plugin_api_version_mismatch_rejected(tmp_path):
    (tmp_path / "bad.py").write_text("API_VERSION = 99\nNAME = 'bad'\n")
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    assert manager.get("bad") is None
    assert any(name == "bad.py" for name, _exc in manager.errors)
