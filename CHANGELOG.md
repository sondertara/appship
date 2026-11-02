# Changelog

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
