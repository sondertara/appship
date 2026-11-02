import pytest

from appship import checks
from appship.util import AppShipError


def test_electron_generic_uses_arch_channel(monkeypatch):
    seen = {}

    def fake_request(url, headers=None, timeout=20, limit=None, method="GET"):
        seen["url"] = url
        body = (
            "version: 1.2.0\n"
            "files:\n"
            "  - url: https://example.test/App.AppImage\n"
            "    sha512: abc123\n"
            "    size: 10\n"
            "path: https://example.test/App.AppImage\n"
            "sha512: abc123\n"
        ).encode()
        return 200, body, {}

    monkeypatch.setattr(checks, "http_request", fake_request)
    update = checks.electron_generic("https://example.test/manifest/", "1.1.0", channel="latest-x64")
    assert seen["url"] == "https://example.test/manifest/latest-x64-linux.yml"
    assert update.status == "update"
    assert update.remote == "1.2.0"
    assert update.download_url == "https://example.test/App.AppImage"
    assert update.filename == "App.AppImage"
    assert update.checksums == {"sha512": "abc123"}
    assert update.kind == "appimage"


def test_electron_generic_rejects_empty_manifest(monkeypatch):
    monkeypatch.setattr(checks, "http_request", lambda *a, **k: (404, b"", {}))
    with pytest.raises(AppShipError):
        checks.electron_generic("https://example.test/manifest/", "1.1.0")
