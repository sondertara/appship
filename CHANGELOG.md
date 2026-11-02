# Changelog

## 0.2.0

- Plugin API v1: backend plugins, availability/system detection, API version
  handshake, `REQUIRES_BACKEND`, structured plugin status, and `--no-plugins`.
- deb → pacman is now an optional built-in backend plugin (`deb`), enabled only
  when `pacman`, `makepkg`, `bsdtar`, `fakeroot`, and `sudo` are present.
- Core `installer`/`registry`/`doctor` no longer import pacman-specific logic.
- `codebuddy` declares `REQUIRES_BACKEND = 'deb'` and preserves
  `chrome-sandbox` setuid on repacks.
- `scan` only includes `.deb` when the deb backend is available.

## 0.1.0

- First public release.
- Subcommand CLI: `install`, `scan`, `list`, `check`, `upgrade`, `uninstall`,
  `rollback`, `history`, `repair`, `migrate`, `set-source`, `adopt`, `plugins`,
  `doctor`, `version`, and `help`.
- Packaged as `appship` with the `appship` console script.
- XDG paths for installed apps, registry, plugins, deb builds, and cache.
- Legacy `~/local` detection plus `appship migrate --dry-run/--apply`.
- Built-in plugins (`antigravity`, `codebuddy`, `wechat`) with user overrides
  from the XDG plugin directory.
- `install.sh` standalone zipapp installer with SHA256 verification,
  mirror/offline support, and `--uninstall`.
- `doctor`, JSON output, and `check --exit-code`.
- Safer process matching for uninstall/rollback.
