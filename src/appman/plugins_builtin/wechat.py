"""WeChat for Linux (official AppImage).

Tencent publishes a single fixed URL with no version in it, so the check compares the server's
content MD5 (x-cos-meta-md5 header) against the installed file. The MD5 of the installed file is
cached in the registry by size+mtime so it is hashed only once per version.
When the server copy differs, the new version is read from the downloaded AppImage at install time.
"""

import os

from appman.api import HOST_ARCH, AppManError, Update, http_head

NAME = "wechat"
DESCRIPTION = "微信 Linux 官方 AppImage（固定下载地址，按服务器 MD5 判断是否有新版）"

_URLS = {
    "x86_64": "https://dldir1.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.AppImage",
    "aarch64": "https://dldir1.qq.com/weixin/Universal/Linux/WeChatLinux_arm64.AppImage",
}


def match(app):
    return app.kind == "appimage" and app.id == "wechat"


def _local_md5(app):
    import hashlib

    path = app.exec_path
    st = os.stat(path)
    sig = f"{st.st_size}:{int(st.st_mtime)}"
    cached = app.data.get("plugin_cache", {})
    if cached.get("sig") == sig and cached.get("md5"):
        return cached["md5"], False
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    app.data["plugin_cache"] = {"sig": sig, "md5": h.hexdigest()}
    return h.hexdigest(), True


def check(app):
    url = _URLS.get(HOST_ARCH)
    if not url:
        raise AppManError(f"微信没有 {HOST_ARCH} 版本")
    if not app.installed:
        raise AppManError("找不到已安装的 AppImage")
    headers = http_head(url)
    remote_md5 = (headers.get("x-cos-meta-md5") or headers.get("etag", "")).strip('"').lower()
    if len(remote_md5) != 32:
        raise AppManError("微信下载服务器没有返回 MD5，无法判断版本")
    local_md5, _ = _local_md5(app)
    src = "微信官方下载 (MD5 比对)"
    if remote_md5 == local_md5:
        return Update("current", local=app.version, remote=app.version, source=src)
    modified = headers.get("last-modified", "")
    return Update(
        "update",
        local=app.version,
        remote=f"新版 ({modified[5:16]})" if modified else "新版",
        download_url=url,
        filename=os.path.basename(url),
        checksums={"md5": remote_md5},
        source=src,
        kind="appimage",
        force=True,
    )
