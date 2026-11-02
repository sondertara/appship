#!/bin/sh
# Install appship from a GitHub release as a standalone zipapp.
# Per-user, no sudo, keeps user data, verifies SHA256 when downloading.
set -eu
umask 022

REPO="${APPSHIP_REPO:-sondertara/appship}"
VERSION="${APPSHIP_VERSION:-latest}"
PREFIX="${APPSHIP_PREFIX:-$HOME/.local}"
PYTHON="${PYTHON:-python3}"
LOCAL_PYZ="${APPSHIP_PYZ:-}"
EXPECTED_SHA="${APPSHIP_SHA256:-}"

DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/appship"
VERSIONS_DIR="$DATA_ROOT/versions"
BIN_DIR="$PREFIX/bin"

usage() {
    cat <<EOF
Usage: install.sh [--prefix DIR] [--version VERSION] [--uninstall]

Environment:
  APPSHIP_REPO     GitHub owner/repo (default: $REPO)
  APPSHIP_VERSION  release version (default: latest)
  APPSHIP_PREFIX   install prefix for the command (default: ~/.local)
  APPSHIP_PYZ      use a local appship.pyz (offline install)
  APPSHIP_SHA256   expected SHA256 for a local/remote appship.pyz
  APPSHIP_BASE_URL mirror/testing base containing appship.pyz + SHA256SUMS
  PYTHON          Python >= 3.11 interpreter (default: python3)

Code is installed under $VERSIONS_DIR/<version>/ and linked from $BIN_DIR/appship.
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

uninstall_appship() {
    if [ -L "$BIN_DIR/appship" ]; then
        target=$(readlink "$BIN_DIR/appship" 2>/dev/null || true)
        case "$target" in
            "$VERSIONS_DIR"/*)
                rm -f "$BIN_DIR/appship"
                ;;
            *)
                die "$BIN_DIR/appship 指向未知位置，未删除: $target"
                ;;
        esac
    elif [ -e "$BIN_DIR/appship" ]; then
        die "$BIN_DIR/appship 不是 appship 安装器创建的软链，未删除"
    fi
    rm -rf "$VERSIONS_DIR"
    echo "✅ 已移除 appship 代码。用户应用、注册表、插件、配置和缓存保留。"
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
    uninstall_appship
    exit 0
fi

command -v "$PYTHON" >/dev/null 2>&1 || die "找不到 Python: $PYTHON"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "需要 Python >= 3.11"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/appship-install.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM

if [ -n "$LOCAL_PYZ" ]; then
    [ -f "$LOCAL_PYZ" ] || die "找不到 APPSHIP_PYZ: $LOCAL_PYZ"
    cp "$LOCAL_PYZ" "$tmp/appship.pyz"
else
    if [ -n "${APPSHIP_BASE_URL:-}" ]; then
        base="${APPSHIP_BASE_URL%/}"
    elif [ "$VERSION" = "latest" ]; then
        base="https://github.com/$REPO/releases/latest/download"
    else
        tag="v${VERSION#v}"
        base="https://github.com/$REPO/releases/download/$tag"
    fi
    echo "⬇ 下载 appship ($VERSION) from $REPO"
    fetch "$base/appship.pyz" "$tmp/appship.pyz"
    fetch "$base/SHA256SUMS" "$tmp/SHA256SUMS"
    expected=$(awk '$2 == "appship.pyz" {print $1; exit}' "$tmp/SHA256SUMS")
    [ -n "$expected" ] || die "SHA256SUMS 中没有 appship.pyz"
    [ "$expected" = "$(sha256 "$tmp/appship.pyz")" ] || die "appship.pyz SHA256 校验失败"
    echo "🔐 SHA256 校验通过"
fi

if [ -n "$EXPECTED_SHA" ]; then
    [ "$EXPECTED_SHA" = "$(sha256 "$tmp/appship.pyz")" ] || die "APPSHIP_SHA256 校验失败"
    echo "🔐 APPSHIP_SHA256 校验通过"
elif [ -z "$LOCAL_PYZ" ]; then
    : # already verified against SHA256SUMS
else
    echo "⚠️ 本地 APPSHIP_PYZ 未校验；如需校验请设置 APPSHIP_SHA256"
fi

ver=$("$PYTHON" "$tmp/appship.pyz" --version | awk '{print $2}')
[ -n "$ver" ] || die "无法读取 appship 版本"
[ "$ver" != "latest" ] || die "无效版本: $ver"

mkdir -p "$VERSIONS_DIR/$ver" "$BIN_DIR"
[ -w "$BIN_DIR" ] || die "$BIN_DIR 不可写"

if [ -e "$BIN_DIR/appship" ] && [ ! -L "$BIN_DIR/appship" ]; then
    backup="$BIN_DIR/appship.bak-$(date +%Y%m%d%H%M%S)"
    mv "$BIN_DIR/appship" "$backup"
    echo "🛡️ 已备份旧入口: $backup"
fi

install -m 0755 "$tmp/appship.pyz" "$VERSIONS_DIR/$ver/appship.pyz" 2>/dev/null \
    || { cp "$tmp/appship.pyz" "$VERSIONS_DIR/$ver/appship.pyz"; chmod 0755 "$VERSIONS_DIR/$ver/appship.pyz"; }
ln -sfn "$ver" "$VERSIONS_DIR/current"
ln -sfn "$VERSIONS_DIR/current/appship.pyz" "$BIN_DIR/appship"

echo "✅ appship $ver 已安装: $BIN_DIR/appship"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "💡 请把 $BIN_DIR 加入 PATH" ;;
esac
echo "💡 运行 'appship doctor' 检查环境；旧 ~/local 布局可用 'appship migrate --dry-run'。"
