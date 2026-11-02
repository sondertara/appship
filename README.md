# appman

User-level AppImage / deb application manager for Linux. It installs apps,
checks upstream releases, upgrades, rolls back, and keeps a small JSON registry.

`appman` is a CLI; the PyPI distribution name is **`appman-cli`** because
`appman` on PyPI belongs to an unrelated project. The installed command is
still `appman`.

## Install

### uv (recommended)

```bash
uv tool install appman-cli
# or, from a checkout:
uv tool install .
```

Upgrade/remove:

```bash
uv tool upgrade appman-cli
uv tool uninstall appman-cli
```

### install.sh (standalone zipapp)

```bash
curl -fsSLO https://github.com/sondertara/appman/releases/latest/download/install.sh
less install.sh
sh install.sh
```

Pin a version or use a mirror:

```bash
APPMAN_VERSION=1.0.0 sh install.sh
APPMAN_REPO=owner/repo sh install.sh
APPMAN_BASE_URL=http://127.0.0.1:8000 sh install.sh   # mirror / local testing
```

The installer downloads `appman.pyz`, verifies `SHA256SUMS`, installs it under
`~/.local/share/appman/versions/<version>/`, and links `~/.local/bin/appman`.
It never uses `sudo` and keeps user data on uninstall. For a development
checkout, `APPMAN_PYZ=dist/appman.pyz sh scripts/install.sh` works offline.

## Usage

```bash
appman install Foo.AppImage
appman scan ~/Downloads
appman list
appman check
appman upgrade
appman uninstall antigravity
appman rollback antigravity
appman history antigravity
appman repair
appman set-source antigravity github:owner/repo
appman adopt codebuddy-cn --plugin codebuddy
appman plugins
appman doctor
appman version
```

Use `appman help COMMAND` for per-command help. Machine-readable output is
available on `list`, `check`, `history`, `plugins`, and `doctor` with `--json`.
`check --exit-code` returns 1 when updates exist, 2 on errors, 0 otherwise.

## Paths (XDG)

| Purpose | Path |
|---|---|
| Command | `~/.local/bin/appman` |
| Code (standalone) | `~/.local/share/appman/versions/<version>/` |
| Installed apps | `~/.local/share/appman/apps/<id>/` |
| Registry | `~/.local/share/appman/apps.json` |
| Plugins | `~/.local/share/appman/plugins/` |
| deb builds | `~/.local/share/appman/debpkgs/` |
| Config | `~/.config/appman/`（预留；当前用 `APPMAN_*` 环境变量） |
| State | `~/.local/state/appman/`（预留：锁/日志） |
| Cache | `~/.cache/appman/` |
| Desktop entries | `~/.local/share/applications/` |
| Icons | `~/.local/share/icons/` |

`XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` are
honored. Override individual paths with `APPMAN_APPS_DIR`, `APPMAN_REGISTRY`,
`APPMAN_PLUGINS_DIR`, `APPMAN_DEB_DIR`, and `APPMAN_CACHE_DIR`.

### Legacy `~/local` layout

Existing installations that still have `~/local/.apps.json` are detected and
used in legacy mode automatically. To move them to XDG:

```bash
appman migrate --dry-run
appman migrate --apply
```

The migration moves registered app directories, rewrites registry/desktop
paths, copies user plugins, backs up the old registry, and leaves unrelated
files in `~/local` alone.

## Plugins

Built-in plugins live in the package (`antigravity`, `codebuddy`, `wechat`).
User plugins are loaded from `~/.local/share/appman/plugins/` (and the legacy
`~/local/appman.d/plugins/`). A user plugin with the same `NAME` overrides the
built-in plugin. Plugins are arbitrary Python code: only install ones you trust.

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
uv build
./scripts/build.sh        # wheel/sdist + dist/appman.pyz + SHA256SUMS
```

The CLI core is stdlib-only; `7z` is needed for AppImage metadata, `pacman`
for the deb backend, and desktop tooling for menu/icon refresh.

## License

MIT.
