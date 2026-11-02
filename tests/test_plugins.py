from appship.plugins import PluginManager


def test_builtin_plugins_load(tmp_path):
    manager = PluginManager(plugins_dirs=[str(tmp_path)])
    names = {p.name for p in manager.plugins}
    assert {"antigravity", "codebuddy", "wechat"} <= names
    antigravity = manager.get("antigravity")
    assert antigravity is not None
    assert antigravity.source == "builtin"
    assert "check" in antigravity.hooks()


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
