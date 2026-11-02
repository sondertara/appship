from appship.desktop import write_desktop_entry


def test_wayland_electron_uses_text_input_v3(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    desktop = tmp_path / "antigravity.desktop"
    meta = {
        "app_name": "Antigravity",
        "comment": "Antigravity - Agentic Desktop Application",
        "categories": "Development;",
        "wm_class": "Antigravity",
        "mime_type": "x-scheme-handler/antigravity;",
    }
    write_desktop_entry(meta, "/tmp/Antigravity.AppImage", None, str(desktop))
    text = desktop.read_text()
    assert "--enable-wayland-ime" in text
    assert "--wayland-text-input-version=3" in text
    assert "--wayland-text-input-version=1" not in text
    assert 'Exec="/tmp/Antigravity.AppImage"' in text


def test_x11_electron_does_not_add_wayland_ime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    desktop = tmp_path / "app.desktop"
    meta = {"app_name": "App", "comment": "Electron app", "categories": "Utility;", "wm_class": "App"}
    write_desktop_entry(meta, "/tmp/App.AppImage", None, str(desktop))
    assert "--enable-wayland-ime" not in desktop.read_text()
