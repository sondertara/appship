"""Antigravity (Google) — AppImage update check.

The bundled app-update.yml only declares `provider: generic` and the manifest URL; it has no
channel. Antigravity sets `autoUpdater.channel = latest-<arch>` at runtime, so electron-builder's
generic provider requests `latest-x64-linux.yml`. appship's generic resolver defaults to
`latest-linux.yml`, which 404s, so pin the feed and channel here.

The same entry also covers the pre-migration tarball install if it is ever registered as a dir app.
"""

from appship.api import HOST_ARCH, Update, electron_generic

NAME = "antigravity"
DESCRIPTION = "Antigravity（Google）— 固定 feed + latest-<arch> 渠道检测"

_FEED = "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/"
_CHANNELS = {"x86_64": "latest-x64"}


def match(app):
    return app.id == "antigravity" or (app.kind == "appimage" and app.name == "Antigravity")


def check(app):
    channel = _CHANNELS.get(HOST_ARCH)
    if not channel:
        return Update.none(f"Antigravity 暂时没有 {HOST_ARCH} 的更新渠道", local=app.version)
    res = electron_generic(_FEED, app.version, channel=channel)
    res.source = f"Antigravity 更新源 ({channel})"
    return res
