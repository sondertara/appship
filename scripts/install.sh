#!/bin/sh
# Install appman from a GitHub release as a standalone zipapp.
# Per-user, no sudo, keeps user data, verifies SHA256 when downloading.
set -eu
umask 022

REPO="${APPMAN_REPO:-sondertara/appman}"
VERSION="${APPMAN_VERSION:-latest}"
PREFIX="${APPMAN_PREFIX:-$HOME/.local}"
PYTHON="${PYTHON:-python3}"
LOCAL_PYZ="${APPMAN_PYZ:-}"
EXPECTED_SHA="${APPMAN_SHA256:-}"

DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/appman"
VERSIONS_DIR="$DATA_ROOT/versions"
BIN_DIR="$PREFIX/bin"

usage() {
    cat <<EOF
Usage: install.sh [--prefix DIR] [--version VERSION] [--uninstall]

Environment:
  APPMAN_REPO     GitHub owner/repo (default: $REPO)
  APPMAN_VERSION  release version (default: latest)
  APPMAN_PREFIX   install prefix for the command (default: ~/.local)
  APPMAN_PYZ      use a local appman.pyz (offline install)
  APPMAN_SHA256   expected SHA256 for a local/remote appman.pyz
  APPMAN_BASE_URL mirror/testing base containing appman.pyz + SHA256SUMS
  PYTHON          Python >= 3.11 interpreter (default: python3)

Code is installed under $VERSIONS_DIR/<version>/ and linked from $BIN_DIR/appman.
User apps, registry, plugins, config, and cache are never removed by --uninstall.
EOF
}

die() {
    echo "❌ $*" >&2
    exit 1
}

fetch() {
    url="$1"
    dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$dest" "$url"
    else
        die "需要 curl 或 wget"
    fi
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "需要 sha256sum 或 shasum"
    fi
}

uninstall_appman() {
    if [ -L "$BIN_DIR/appman" ]; then
        target=$(readlink "$BIN_DIR/appman" 2>/dev/null || true)
        case "$target" in
            "$VERSIONS_DIR"/*)
                rm -f "$BIN_DIR/appman"
                ;;
            *)
                die "$BIN_DIR/appman 指向未知位置，未删除: $target"
                ;;
        esac
    elif [ -e "$BIN_DIR/appman" ]; then
        die "$BIN_DIR/appman 不是 appman 安装器创建的软链，未删除"
    fi
    rm -rf "$VERSIONS_DIR"
    echo "✅ 已移除 appman 代码。用户应用、注册表、插件、配置和缓存保留。"
}

UNINSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            [ $# -ge 2 ] || die "--prefix 需要目录"
            PREFIX="$2"
            shift 2
            ;;
        --version)
            [ $# -ge 2 ] || die "--version 需要版本号"
            VERSION="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "未知参数: $1"
            ;;
    esac
done

BIN_DIR="$PREFIX/bin"

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall_appman
    exit 0
fi

command -v "$PYTHON" >/dev/null 2>&1 || die "找不到 Python: $PYTHON"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "需要 Python >= 3.11"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/appman-install.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM

if [ -n "$LOCAL_PYZ" ]; then
    [ -f "$LOCAL_PYZ" ] || die "找不到 APPMAN_PYZ: $LOCAL_PYZ"
    cp "$LOCAL_PYZ" "$tmp/appman.pyz"
else
    if [ -n "${APPMAN_BASE_URL:-}" ]; then
        base="${APPMAN_BASE_URL%/}"
    elif [ "$VERSION" = "latest" ]; then
        base="https://github.com/$REPO/releases/latest/download"
    else
        tag="v${VERSION#v}"
        base="https://github.com/$REPO/releases/download/$tag"
    fi
    echo "⬇ 下载 appman ($VERSION) from $REPO"
    fetch "$base/appman.pyz" "$tmp/appman.pyz"
    fetch "$base/SHA256SUMS" "$tmp/SHA256SUMS"
    expected=$(awk '$2 == "appman.pyz" {print $1; exit}' "$tmp/SHA256SUMS")
    [ -n "$expected" ] || die "SHA256SUMS 中没有 appman.pyz"
    [ "$expected" = "$(sha256 "$tmp/appman.pyz")" ] || die "appman.pyz SHA256 校验失败"
    echo "🔐 SHA256 校验通过"
fi

if [ -n "$EXPECTED_SHA" ]; then
    [ "$EXPECTED_SHA" = "$(sha256 "$tmp/appman.pyz")" ] || die "APPMAN_SHA256 校验失败"
    echo "🔐 APPMAN_SHA256 校验通过"
elif [ -z "$LOCAL_PYZ" ]; then
    : # already verified against SHA256SUMS
else
    echo "⚠️ 本地 APPMAN_PYZ 未校验；如需校验请设置 APPMAN_SHA256"
fi

ver=$("$PYTHON" "$tmp/appman.pyz" --version | awk '{print $2}')
[ -n "$ver" ] || die "无法读取 appman 版本"
[ "$ver" != "latest" ] || die "无效版本: $ver"

mkdir -p "$VERSIONS_DIR/$ver" "$BIN_DIR"
[ -w "$BIN_DIR" ] || die "$BIN_DIR 不可写"

if [ -e "$BIN_DIR/appman" ] && [ ! -L "$BIN_DIR/appman" ]; then
    backup="$BIN_DIR/appman.bak-$(date +%Y%m%d%H%M%S)"
    mv "$BIN_DIR/appman" "$backup"
    echo "🛡️ 已备份旧入口: $backup"
fi

install -m 0755 "$tmp/appman.pyz" "$VERSIONS_DIR/$ver/appman.pyz" 2>/dev/null \
    || { cp "$tmp/appman.pyz" "$VERSIONS_DIR/$ver/appman.pyz"; chmod 0755 "$VERSIONS_DIR/$ver/appman.pyz"; }
ln -sfn "$ver" "$VERSIONS_DIR/current"
ln -sfn "$VERSIONS_DIR/current/appman.pyz" "$BIN_DIR/appman"

echo "✅ appman $ver 已安装: $BIN_DIR/appman"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "💡 请把 $BIN_DIR 加入 PATH" ;;
esac
echo "💡 运行 'appman doctor' 检查环境；旧 ~/local 布局可用 'appman migrate --dry-run'。"
