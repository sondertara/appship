# appship

User-level AppImage / deb application manager for Linux. It installs apps,
checks upstream releases, upgrades, rolls back, and keeps a small JSON registry.

`appship` is both the PyPI distribution and the installed command.

## Install

### uv (recommended)

```bash
uv tool install appship
# or, from a checkout:
uv tool install .
```

Upgrade/remove:

```bash
uv tool upgrade appship
uv tool uninstall appship
```

### install.sh (standalone zipapp)

```bash
curl -fsSLO https://github.com/sondertara/appship/releases/latest/download/install.sh
less install.sh
sh install.sh
```

Pin a version or use a mirror:

```bash
APPSHIP_VERSION=0.1.0 sh install.sh
APPSHIP_REPO=owner/repo sh install.sh
APPSHIP_BASE_URL=http://127.0.0.1:8000 sh install.sh   # mirror / local testing
```

The installer downloads `appship.pyz`, verifies `SHA256SUMS`, installs it under
`~/.local/share/appship/versions/<version>/`, and links `~/.local/bin/appship`.
It never uses `sudo` and keeps user data on uninstall. For a development
checkout, `APPSHIP_PYZ=dist/appship.pyz sh scripts/install.sh` works offline.

## Usage

```bash
appship install Foo.AppImage
appship scan ~/Downloads
appship list
appship check
appship upgrade
appship uninstall antigravity
appship rollback antigravity
appship history antigravity
appship repair
appship set-source antigravity github:owner/repo
appship adopt codebuddy-cn --plugin codebuddy
appship plugins
appship doctor
appship version
```

Use `appship help COMMAND` for per-command help. Machine-readable output is
available on `list`, `check`, `history`, `plugins`, and `doctor` with `--json`.
`check --exit-code` returns 1 when updates exist, 2 on errors, 0 otherwise.

## Paths (XDG)

| Purpose | Path |
|---|---|
| Command | `~/.local/bin/appship` |
| Code (standalone) | `~/.local/share/appship/versions/<version>/` |
| Installed apps | `~/.local/share/appship/apps/<id>/` |
| Registry | `~/.local/share/appship/apps.json` |
| Plugins | `~/.local/share/appship/plugins/` |
| deb builds | `~/.local/share/appship/debpkgs/` |
| Config | `~/.config/appship/`（预留；当前用 `APPSHIP_*` 环境变量） |
| State | `~/.local/state/appship/`（预留：锁/日志） |
| Cache | `~/.cache/appship/` |
| Desktop entries | `~/.local/share/applications/` |
| Icons | `~/.local/share/icons/` |

`XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` are
honored. Override individual paths with `APPSHIP_APPS_DIR`, `APPSHIP_REGISTRY`,
`APPSHIP_PLUGINS_DIR`, `APPSHIP_DEB_DIR`, and `APPSHIP_CACHE_DIR`.

### Legacy `~/local` layout

Existing installations that still have `~/local/.apps.json` are detected and
used in legacy mode automatically. To move them to XDG:

```bash
appship migrate --dry-run
appship migrate --apply
```

The migration moves registered app directories, rewrites registry/desktop
paths, copies user plugins, backs up the old registry, and leaves unrelated
files in `~/local` alone.

## Plugins

Built-in plugins live in the package (`antigravity`, `codebuddy`, `wechat`).
User plugins are loaded from `~/.local/share/appship/plugins/` (and the legacy
`~/local/appman.d/plugins/`). A user plugin with the same `NAME` overrides the
built-in plugin. Plugins are arbitrary Python code: only install ones you trust.

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
uv build
./scripts/build.sh        # wheel/sdist + dist/appship.pyz + SHA256SUMS
```

The CLI core is stdlib-only; `7z` is needed for AppImage metadata, `pacman`
for the deb backend, and desktop tooling for menu/icon refresh.

## License

MIT.
