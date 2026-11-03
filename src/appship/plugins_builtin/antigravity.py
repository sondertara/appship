"""Antigravity (Google) — AppImage update check.

The bundled app-update.yml only declares `provider: generic` and the manifest URL; it has no
channel. Antigravity sets `autoUpdater.channel = latest-<arch>` at runtime, so electron-builder's
generic provider requests `latest-x64-linux.yml`. appship's generic resolver defaults to
`latest-linux.yml`, which 404s, so pin the feed and channel here.

The same entry also covers the pre-migration tarball install if it is ever registered as a dir app.
"""

import os

from appship.api import HOST_ARCH, Update, electron_generic

NAME = "antigravity"
DESCRIPTION = "Antigravity（Google）— Stable / Insiders 双渠道检测"

_STABLE_FEED = "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/"
_INSIDERS_FEED = "https://storage.googleapis.com/antigravity-public/insiders/"
_CHANNELS = {"x86_64": "latest-x64"}


def _is_insiders_meta(name, version="", filename=""):
    low_name = (name or "").lower()
    low_file = (filename or "").lower()
    return "insiders" in low_name or "insiders" in low_file or str(version or "").startswith("1.0.20")


def _is_insiders(app):
    if _is_insiders_meta(app.name, app.version) or "insiders" in app.id.lower():
        return True
    src_url = (app.data.get("update_source") or {}).get("url") or ""
    if "/insiders" in src_url:
        return True
    history = app.data.get("history") or []
    return bool(history and "insiders" in (history[-1].get("file") or "").lower())


def match_file(path, info):
    if info.get("kind") != "appimage":
        return False
    meta = info.get("meta") or {}
    name = meta.get("app_name") or os.path.basename(path)
    return name.lower().startswith("antigravity")


def app_id(meta):
    if _is_insiders_meta(meta.get("app_name"), meta.get("version"), meta.get("filename")):
        return "antigravity-insiders"
    return "antigravity"


def match(app):
    return app.id in ("antigravity", "antigravity-insiders") or (
        app.kind == "appimage" and app.name.lower().startswith("antigravity")
    )


def check(app):
    channel = _CHANNELS.get(HOST_ARCH)
    if not channel:
        return Update.none(f"Antigravity 暂时没有 {HOST_ARCH} 的更新渠道", local=app.version)
    insiders = _is_insiders(app)
    feed = _INSIDERS_FEED if insiders else _STABLE_FEED
    label = f"Insiders ({channel})" if insiders else f"Stable ({channel})"
    res = electron_generic(feed, app.version, channel=channel)
    res.source = f"Antigravity 更新源 [{label}]"
    return res
